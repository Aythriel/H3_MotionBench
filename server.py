"""Loopback-only H3 workbench for the ComfyUI H3 architectures."""
from __future__ import annotations

import argparse
import copy
import asyncio
import hashlib
import io
import json
import math
import os
import secrets
import shutil
import tempfile
import string
import subprocess
import sys
import time
import uuid
from pathlib import Path

import av

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))

from storage import atomic_json, local_folder, writable_folder, clip_name
from assembly import Assembly
from dependencies import Dependencies, read_config
from setup_api import SetupAPI
from conditioning import (ARCHITECTURES, MODES, PRESETS, B25, model_architecture, model_loader,
                          mode_spec, input_roles, validate_conditioning)

HERE = Path(__file__).resolve().parent
ROOT = HERE
LOCAL_CONFIG, _ = read_config(HERE / "config.local.json")
DEFAULT_COMFY = Path(os.environ.get("H3_COMFY_HOME") or LOCAL_CONFIG.get("comfy_home") or str(HERE / "dependencies/ComfyUI_windows_portable"))
ACTIVE = {"queued", "running", "cancelling", "recovering"}
DEFAULTS = {
    "output_folder": "",
    "name": "Motion study", "mode": "first_reference", "first": "", "last": "",
    "architecture": "hybrid", "identity_images": [], "ref_image_size": "match", "settings_version": 3,
    "prompt": "One continuous shot. Continue the pose and composition of the starting frame. Preserve the face, hair, clothing, equipment, proportions and rendering style of the character references. Keep the whole figure and all equipment inside the frame. Locked camera, fixed scale, unchanged background, constant lighting.",
    "sound": "No sound specified.", "music": "No music.",
    "width": 608, "height": 352, "seconds": 2, "steps": 20,
    "seed": 1234567, "random_seed": False, "sampler": "res_multistep", "scheduler": "simple",
    "denoise": 1.0, "visual_strength": 0.999, "encoder_device": "default",
    "free_encoder": True, "tiled": False, "lora": "", "lora_strength": 1.0,
    "fit": "contain", "background": "#00ff00", "evict_after": False,
    "model": B25,
    "encoder": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "video_vae": "minimax_h3_video_vae_fp16.safetensors",
    "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
}



def copy_video(source, destination):
    if destination.exists():
        with source.open("rb") as a, destination.open("rb") as b:
            if hashlib.file_digest(a, "sha256").digest() == hashlib.file_digest(b, "sha256").digest():
                return
        raise ValueError(f"A different file already exists: {destination}")
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".partial", delete=False) as stream:
        temp = Path(stream.name)
        try:
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, stream)
        except Exception:
            stream.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.rename(destination)
    finally:
        temp.unlink(missing_ok=True)



def extract_video_frame(path, endpoint):
    if endpoint not in ("first", "last"):
        raise ValueError("Choose the first or last video frame.")
    try:
        with av.open(str(path)) as video:
            if not video.streams.video:
                raise ValueError("This file has no video stream.")
            video.streams.video[0].thread_count = 2
            chosen = None
            for frame in video.decode(video=0):
                chosen = frame
                if endpoint == "first":
                    break
            if chosen is None:
                raise ValueError("This video contains no decodable frames.")
            stream = io.BytesIO()
            chosen.to_image().save(stream, format="PNG")
            return stream.getvalue()
    except av.FFmpegError as exc:
        raise ValueError(f"Could not extract the {endpoint} frame. The video may be incomplete or damaged.") from exc


def finish_timing(job, messages):
    """Prefer engine timestamps so reconnects do not inflate generation time."""
    stamps = {}
    for event, data in messages:
        value = data.get("timestamp")
        if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
            stamps[event] = value / 1000
    started = stamps.get("execution_start", job.get("started"))
    finished = next((stamps[event] for event in ("execution_success", "execution_error", "execution_interrupted")
                     if event in stamps), time.time())
    if started is not None and finished >= started:
        job["started"] = started
    job["finished"] = finished


def frame_count(seconds):
    n = max(5, math.floor(seconds * 24 + 0.5))
    return n + (5 - n % 17) % 17


def choices(info, node, field):
    schema = info.get(node, {}).get("input", {})
    spec = {**schema.get("required", {}), **schema.get("optional", {})}.get(field, [])
    if spec and isinstance(spec[0], list):
        return spec[0]
    return spec[1].get("options", []) if len(spec) > 1 else []


def weight_footprints(home, options):
    folders = {"model": ("unet", "diffusion_models"), "encoder": ("text_encoders", "clip"),
               "video_vae": ("vae",), "audio_vae": ("vae",), "lora": ("loras",)}
    result = {}
    for component, locations in folders.items():
        result[component] = {}
        for name in options.get(component, []):
            size = None
            for location in locations:
                root = (home / "ComfyUI/models" / location).resolve()
                path = (root / name).resolve()
                if path.is_relative_to(root) and path.is_file():
                    size = path.stat().st_size
                    break
            result[component][name] = size
    return result


def validate(raw, info, refs):
    if not isinstance(raw, dict):
        raise ValueError("Settings must be an object.")
    c = {key: raw.get(key, value) for key, value in DEFAULTS.items()}
    for field in ("name", "prompt", "sound", "music", "first", "last", "output_folder"):
        if not isinstance(c[field], str) or len(c[field]) > 16000:
            raise ValueError(f"Invalid {field}.")
    c["name"] = c["name"][:100].strip() or "Untitled take"
    if not c["prompt"].strip():
        raise ValueError("Describe the motion before generating.")
    for field, low, high, integral in [
        ("width", 64, 1536, True), ("height", 64, 1536, True),
        ("seconds", 0.2, 15, False), ("steps", 1, 100, True),
        ("seed", 0, 9007199254740991, True), ("denoise", 0.01, 1, False),
        ("visual_strength", 0, 1, False), ("lora_strength", 0, 2, False),
    ]:
        value = float(c[field])
        if not math.isfinite(value) or not low <= value <= high or (integral and value != int(value)):
            raise ValueError(f"{field} must be {'an integer ' if integral else ''}between {low} and {high}.")
        c[field] = int(value) if integral else value
    if c["width"] % 32 or c["height"] % 32 or c["width"] * c["height"] > 1344 * 768:
        raise ValueError("Dimensions must be multiples of 32, at most 1,032,192 pixels.")
    for field in ("random_seed", "free_encoder", "tiled", "evict_after"):
        if not isinstance(c[field], bool):
            raise ValueError(f"{field} must be true or false.")
    for field, allowed in [("fit", ("contain", "cover", "stretch")),
                           ("encoder_device", ("default", "cpu"))]:
        if c[field] not in allowed:
            raise ValueError(f"Invalid {field}.")
    if not isinstance(c["background"], str) or len(c["background"]) != 7 or c["background"][0] != "#":
        raise ValueError("Background must be a hex color.")
    int(c["background"][1:], 16)
    validate_conditioning(c, raw, info, refs)
    for field, node, input_name in [
        ("model", *model_loader(c["model"])), ("encoder", "CLIPLoader", "clip_name"),
        ("video_vae", "VAELoader", "vae_name"), ("audio_vae", "VAELoader", "vae_name"),
        ("sampler", "KSamplerSelect", "sampler_name"), ("scheduler", "BasicScheduler", "scheduler"),
    ]:
        if c[field] not in choices(info, node, input_name):
            raise ValueError(f"Selected {field} is unavailable; refresh the connection.")
    if "minimax" not in c["encoder"].lower() or "video" not in c["video_vae"].lower() or "audio" not in c["audio_vae"].lower():
        raise ValueError("Select the H3 encoder and the matching video/audio VAEs.")
    if c["lora"] and c["lora"] not in choices(info, "LoraLoaderModelOnly", "lora_name"):
        raise ValueError("Selected LoRA is unavailable.")
    if c["free_encoder"] and "H3FreeTextEncoder" not in info:
        raise ValueError("H3FreeTextEncoder is missing. Disable encoder eviction or install the H3 pack.")
    if c["mode"] != "text" and c["visual_strength"] != 0.999 and "H3ConditionStrength" not in info:
        raise ValueError("The reference strength control requires H3ConditionStrength.")
    if c["random_seed"]:
        c["seed"] = secrets.randbelow(2**48)
    c["frames"] = frame_count(c["seconds"])
    c["actual_seconds"] = c["frames"] / 24
    return c


def build_graph(c, uploaded, prefix, info):
    graph = {}

    def node(key, kind, title, **inputs):
        graph[key] = {"class_type": kind, "_meta": {"title": title}, "inputs": inputs}
        return [key, 0]

    loader, model_input = model_loader(c["model"])
    loader_inputs = {model_input: c["model"]}
    if loader == "UNETLoader":
        loader_inputs["weight_dtype"] = "default"
    model = node("model", loader, "Load H3 checkpoint", **loader_inputs)
    if c["lora"]:
        model = node("lora", "LoraLoaderModelOnly", "Apply LoRA", model=model,
                     lora_name=c["lora"], strength_model=c["lora_strength"])
    clip = node("encoder", "CLIPLoader", "Load visual/text encoder", clip_name=c["encoder"],
                type="minimax", device=c["encoder_device"])
    vae = node("video_vae", "VAELoader", "Load video VAE", vae_name=c["video_vae"])
    audio = node("audio_vae", "VAELoader", "Load audio VAE", vae_name=c["audio_vae"])
    spec = mode_spec(c)
    inputs = {"clip": clip, "vae": vae, "width": c["width"], "height": c["height"], "length": c["frames"]}
    for slot in spec["frames"]:
        inputs[slot + "_frame"] = node(slot, "LoadImage", f"Load {slot} frame", image=uploaded[slot])
    prompt = {"integrated_multimodal_description": c["prompt"],
              "overall_soundscape": c["sound"], "non_diegetic_music": c["music"]}
    if spec["references"]:
        pictures = []
        for index, _ in enumerate(c["identity_images"], 1):
            key = f"identity_{index}"
            inputs[f"ref_images.ref_image_{index}"] = node(key, "LoadImage", f"Character reference {index}", image=uploaded[key])
            pictures.append(f"<Picture {index + ('first' in spec['frames'])}>")
        prompt["subject_definitions"] = "<Subject 1> is the character depicted in " + ", ".join(pictures) + ". Preserve this character's appearance, clothing, proportions and rendering style. These images describe appearance, not an ending pose."
        prompt = {"subject_definitions": prompt.pop("subject_definitions"), **prompt}
    if c["architecture"] != "fl2va":
        inputs.update(audio_vae=audio, ref_image_size=c["ref_image_size"])
    if c["architecture"] == "hybrid":
        inputs["first_frame_first"] = True
    inputs["prompt"] = json.dumps(prompt, ensure_ascii=False)
    cond = node("condition", ARCHITECTURES[c["architecture"]]["node"], "Encode prompt, frames and references", **inputs)
    if (spec["frames"] or spec["references"]) and "H3ConditionStrength" in info:
        cond = node("strength", "H3ConditionStrength", "Visual adherence", conditioning=cond,
                    visual_strength=c["visual_strength"], audio_strength=1.0)
    if c["free_encoder"]:
        cond = node("free_encoder", "H3FreeTextEncoder", "Evict encoder before sampling", conditioning=cond, clip=clip)
    noise = node("noise", "RandomNoise", "Set seed", noise_seed=c["seed"])
    sigmas = node("schedule", "BasicScheduler", "Sampling schedule", model=model,
                  scheduler=c["scheduler"], steps=c["steps"], denoise=c["denoise"])
    sampler = node("sampler", "KSamplerSelect", "Select sampler", sampler_name=c["sampler"])
    guider = node("guider", "BasicGuider", "H3 guidance", model=model, conditioning=cond)
    samples = node("sample", "SamplerCustomAdvanced", "Generate video and audio", noise=noise,
                   guider=guider, sampler=sampler, sigmas=sigmas, latent_image=["condition", 1])
    if c["tiled"]:
        video = node("decode", "VAEDecodeTiled", "Decode video in tiles", samples=samples, vae=vae,
                     tile_size=512, overlap=64, temporal_size=64, temporal_overlap=8)
    else:
        video = node("decode", "VAEDecode", "Decode video", samples=samples, vae=vae)
    sound = node("decode_audio", "VAEDecodeAudio", "Decode native audio", samples=samples, vae=audio)
    av = node("mux", "CreateVideo", "Combine video and audio", images=video, audio=sound, fps=24, bit_depth=8)
    node("save", "SaveVideo", "Save MP4 with audio", video=av, filename_prefix=prefix, format="mp4", codec="h264")
    missing = sorted({item["class_type"] for item in graph.values()} - info.keys())
    if missing:
        raise ValueError("Missing ComfyUI nodes: " + ", ".join(missing))
    return graph


def prepare_image(path, c, fit_to_canvas=True):
    with Image.open(path) as source:
        rgba = ImageOps.exif_transpose(source).convert("RGBA")
        base = Image.new("RGBA", rgba.size, c["background"])
        base.alpha_composite(rgba)
        image = base.convert("RGB")
        size = (c["width"], c["height"])
        if not fit_to_canvas:
            pass
        elif c["fit"] == "contain":
            image = ImageOps.pad(image, size, Image.Resampling.LANCZOS, color=c["background"])
        elif c["fit"] == "cover":
            image = ImageOps.fit(image, size, Image.Resampling.LANCZOS)
        else:
            image = image.resize(size, Image.Resampling.LANCZOS)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()


class Workbench:
    def __init__(self, comfy_home, data, port, comfy_port, dependencies=None):
        self.home, self.data, self.port = comfy_home, data, port
        self.base = f"http://127.0.0.1:{comfy_port}"
        self.comfy_port = comfy_port
        for folder in (data, data / "refs", data / "jobs"):
            folder.mkdir(parents=True, exist_ok=True)
        default_folder = str((self.data.parent / "exports").resolve())
        self.output_settings = self.read(data / "output-folders.json", {"default": default_folder, "folders": [default_folder]})
        atomic_json(data / "output-folders.json", self.output_settings)
        self.refs = self.read(data / "references.json", {})
        self.jobs = {}
        for path in (data / "jobs").glob("*.json"):
            job = self.read(path, None)
            if job:
                self.jobs[job["id"]] = job
        self.info, self.progress, self.stats, self.queue = {}, {}, {}, {}
        self.online, self.ws_online, self.error = False, False, "Connecting to ComfyUI"
        client_path = data / "client.json"
        self.client_id = self.read(client_path, {}).get("id") or "h3-motionbench-" + str(uuid.uuid4())
        atomic_json(client_path, {"id": self.client_id})
        self.lock = asyncio.Lock()
        self.process = None
        self.process_base = self.base
        self.evict_pending = False
        self.last_eviction = None
        self.exporting_jobs = set()
        self.assembly = Assembly(self)
        self.dependencies = dependencies
        self.setup = SetupAPI(self, dependencies) if dependencies else None


    @staticmethod
    def read(path, fallback):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return fallback

    def add_reference(self, content, name, source="browser upload"):
        with Image.open(io.BytesIO(content)) as image:
            if image.width * image.height > 32_000_000 or getattr(image, "n_frames", 1) != 1:
                raise ValueError("Use a single image of at most 32 megapixels.")
            image = ImageOps.exif_transpose(image).convert("RGBA")
            stream = io.BytesIO()
            image.save(stream, format="PNG")
            content = stream.getvalue()
            digest = hashlib.sha256(content).hexdigest()
            key = digest[:20]
            (self.data / "refs" / (key + ".png")).write_bytes(content)
            self.refs[key] = {"id": key, "name": name[:120], "width": image.width, "height": image.height,
                              "sha256": digest, "source": source}
            atomic_json(self.data / "references.json", self.refs)
            return self.refs[key]

    def save_job(self, job):
        atomic_json(self.data / "jobs" / (job["id"] + ".json"), job)

    async def rename(self, request):
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Provide a clip name.")
        name = clip_name(body.get("name"))
        async with self.lock:
            job = self.jobs.get(request.match_info["id"])
            if not job:
                raise web.HTTPNotFound()
            if job["status"] in ACTIVE or job["id"] in self.exporting_jobs:
                raise ValueError("Wait for this generation to finish saving before renaming it.")
            updated = copy.deepcopy(job)
            updated.setdefault("original_name", job["config"]["name"])
            updated["config"]["name"] = name
            updated["renamed_at"] = time.time()
            self.save_job(updated)
            self.jobs[job["id"]] = updated
            warning = None
            destination = updated["config"].get("output_folder")
            if destination:
                try:
                    record = local_folder(destination) / (job["id"] + "-record.json")
                    saved = self.read(record, {})
                    if record.is_file() and saved.get("id") == job["id"] and saved.get("prompt_id") == job["prompt_id"]:
                        atomic_json(record, updated)
                except (OSError, ValueError):
                    warning = "Inventory name saved; the destination record could not be updated."
            renamed_refs = dict(self.refs)
            for endpoint in ("first", "last"):
                key = updated.get(f"{endpoint}_frame_reference")
                ref = self.refs.get(key)
                if ref and isinstance(ref.get("source"), dict) and ref["source"].get("job_id") == job["id"]:
                    renamed_refs[key] = {**ref, "name": name + f" - {endpoint} frame"}
            if renamed_refs != self.refs:
                try:
                    atomic_json(self.data / "references.json", renamed_refs)
                    self.refs = renamed_refs
                except OSError:
                    warning = "Clip renamed; its cached frame label could not be updated."
            return web.json_response({"message": warning or "Clip renamed.", "name": name})

    def source_video(self, output, home=None):
        if output.get("type") == "saved":
            raise ValueError("This clip is stored only in its recorded output folder.")
        base = (Path(home or self.home) / "ComfyUI" / ("temp" if output.get("type") == "temp" else "output")).resolve()
        path = (base / output.get("subfolder", "") / output["filename"]).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise ValueError("Video is missing from the ComfyUI output folder.")
        return path

    def video_path(self, job, index=0):
        if index < 0 or index >= len(job.get("outputs", [])):
            raise ValueError("This take has no video at that index.")
        saved = job.get("saved_outputs", [])
        destination = job["config"].get("output_folder")
        if index < len(saved) and destination:
            path = Path(saved[index]).resolve()
            if path.is_relative_to(local_folder(destination)) and path.is_file():
                return path
        return self.source_video(job["outputs"][index], job.get("comfy_home"))

    async def video_frame(self, request):
        endpoint = request.match_info["endpoint"]
        async with self.lock:
            job = self.jobs.get(request.match_info["id"])
            if not job:
                raise web.HTTPNotFound()
            if job["status"] != "completed" or not job.get("outputs"):
                raise ValueError(f"Select a completed take with a video to save its {endpoint} frame.")
            key = job.get(f"{endpoint}_frame_reference")
            if key in self.refs and (self.data / "refs" / (key + ".png")).is_file():
                return web.json_response(self.refs[key])
            path = self.video_path(job)
            content = await asyncio.to_thread(extract_video_frame, path, endpoint)
            ref = self.add_reference(content, job["config"]["name"] + f" - {endpoint} frame",
                                     {"kind": f"video_{endpoint}_frame", "job_id": job["id"], "video": str(path)})
            job[f"{endpoint}_frame_reference"] = ref["id"]
            self.save_job(job)
            return web.json_response(ref)

    def publish_outputs(self, job):
        folder = local_folder(job["config"]["output_folder"])
        writable_folder(folder)
        paths = []
        for output in job["outputs"]:
            source = self.source_video(output, job.get("comfy_home"))
            destination = folder / source.name
            copy_video(source, destination)
            paths.append(str(destination))
        job["saved_outputs"] = paths
        job.pop("export_error", None)
        record = folder / (job["id"] + "-record.json")
        if record.exists():
            previous = self.read(record, {})
            if previous.get("id") != job["id"] or previous.get("prompt_id") != job["prompt_id"]:
                raise ValueError(f"An unrelated record already exists: {record}")
        atomic_json(record, job)

    async def export_job(self, job):
        self.exporting_jobs.add(job["id"])
        try:
            try:
                await asyncio.to_thread(self.publish_outputs, job)
            except (OSError, ValueError) as exc:
                job["export_error"] = f"Video rendered, but saving to the selected folder failed: {exc}"
            self.save_job(job)
        finally:
            self.exporting_jobs.discard(job["id"])

    async def retry_export(self, request):
        async with self.lock:
            job = self.jobs.get(request.match_info["id"])
            if not job or job["status"] != "completed" or not job["config"].get("output_folder"):
                raise ValueError("Only completed takes with a selected destination can be saved again.")
            await self.export_job(job)
            if job.get("export_error"):
                raise ValueError(job["export_error"])
            return web.json_response({"message": "Video and take record saved. No generation was rerun."})

    def media_deletion_paths(self, job):
        if job["id"] in self.exporting_jobs:
            raise ValueError("Wait for this take to finish saving before deleting it.")
        if self.assembly.source_in_use(job["id"]):
            raise ValueError("This video is being used by an assembly export. Wait for it to finish or cancel that export first.")
        paths = []
        for output in job.get("outputs", []):
            try:
                source = self.source_video(output, job.get("comfy_home"))
            except ValueError:
                source = None
            if source is not None and source.is_file():
                paths.append(source)

        destination = job["config"].get("output_folder")
        if job.get("saved_outputs") and not destination:
            raise ValueError("This take has saved copies but no recorded output folder; deletion cannot safely locate them.")
        if destination:
            folder = local_folder(destination)
            for saved in job.get("saved_outputs", []):
                path = Path(saved).resolve()
                if not path.is_relative_to(folder):
                    raise ValueError("Saved output path is outside this take's selected folder.")
                if path.is_file():
                    paths.append(path)

            record = folder / (job["id"] + "-record.json")
            if record.is_file():
                record_data = self.read(record, {})
                if record_data.get("id") != job["id"] or record_data.get("prompt_id") != job["prompt_id"]:
                    raise ValueError("The destination record does not belong to this take.")
                paths.append(record)

        return list(dict.fromkeys(paths))

    def delete_media(self, job):
        if job["status"] != "completed":
            raise ValueError("Only completed takes can have their generated media deleted.")
        paths = self.media_deletion_paths(job)

        deleted = []
        for path in dict.fromkeys(paths):
            path.unlink()
            deleted.append(str(path))
        job["status"] = "discarded"
        job["discarded_at"] = time.time()
        job["deleted_media"] = deleted
        job["outputs"] = []
        job["saved_outputs"] = []
        job.pop("export_error", None)

    def permanent_deletion_paths(self, job):
        if job["status"] not in ("discarded", "failed"):
            raise ValueError("Only discarded or failed takes can be permanently deleted.")
        records = (self.data / "jobs").resolve()
        record = (records / (job["id"] + ".json")).resolve()
        if record.parent != records:
            raise ValueError("The take record is outside the history folder.")
        return [*self.media_deletion_paths(job), record]

    async def purge_jobs(self, jobs):
        # Validate the entire selection before deleting any files. Do not follow
        # deleted_media: those paths may now belong to a different video.
        plans = [(job, self.permanent_deletion_paths(job)) for job in jobs]
        for job, paths in plans:
            def remove_files():
                for path in paths:
                    path.unlink(missing_ok=True)
            await asyncio.to_thread(remove_files)
            del self.jobs[job["id"]]
            self.progress.pop(job["prompt_id"], None)
        return [job["id"] for job in jobs]

    async def empty_trash(self, request):
        async with self.lock:
            discarded = [job for job in self.jobs.values() if job["status"] == "discarded"]
            removed = await self.purge_jobs(discarded)
            return web.json_response({"message": f"Permanently deleted {len(removed)} discarded takes.", "removed": removed})

    async def discard(self, request):
        async with self.lock:
            job = self.jobs.get(request.match_info["id"])
            if not job:
                raise web.HTTPNotFound()
            if job["status"] in ("discarded", "failed"):
                removed = await self.purge_jobs([job])
                return web.json_response({"message": "Take and its settings permanently deleted.", "removed": removed})
            await asyncio.to_thread(self.delete_media, job)
            self.save_job(job)
            return web.json_response({"message": "Generated video copies and destination record deleted. Take settings remain in history."})

    async def output_folders(self, request):
        if request.method == "GET":
            return web.json_response(self.output_settings)
        body = await request.json()
        folder = str(local_folder(body.get("path")))
        async with self.lock:
            action = body.get("action", "add")
            if action not in ("add", "default"):
                raise ValueError("Unknown folder action.")
            if action == "default" and folder not in self.output_settings["folders"]:
                raise ValueError("Add the folder before setting it as default.")
            await asyncio.to_thread(writable_folder, Path(folder))
            updated = {"default": self.output_settings["default"], "folders": list(self.output_settings["folders"])}
            if folder not in updated["folders"]:
                updated["folders"].append(folder)
            if action == "default":
                updated["default"] = folder
            atomic_json(self.data / "output-folders.json", updated)
            self.output_settings = updated
            return web.json_response({**updated, "selected": folder})

    async def browse_folders(self, request):
        value = request.query.get("path", "")
        if not value:
            roots = [f"{letter}:/" for letter in string.ascii_uppercase if Path(f"{letter}:/").is_dir()]
            return web.json_response({"path": "", "parent": "", "folders": roots})
        path = local_folder(value)
        if not path.is_dir():
            raise ValueError("This folder does not exist yet. Use Add folder to create it, or browse its parent.")
        folders = []
        for child in path.iterdir():
            try:
                if child.is_dir() and not child.name.startswith("."):
                    folders.append(str(child))
            except OSError:
                continue
        return web.json_response({"path": str(path), "parent": str(path.parent) if path.parent != path else "",
                                  "folders": sorted(folders, key=str.casefold)})

    async def comfy(self, route, payload=None):
        async with self.session.request("GET" if payload is None else "POST", self.base + route,
                                        json=payload, timeout=ClientTimeout(total=25)) as response:
            result = await response.json(content_type=None)
            if response.status >= 400:
                raise ValueError(json.dumps(result, ensure_ascii=False)[:5000])
            return result

    async def refresh(self):
        self.stats, self.queue = await asyncio.gather(self.comfy("/system_stats"), self.comfy("/queue"))
        if not self.info:
            self.info = await self.comfy("/object_info")
        self.online, self.error = True, ""

    async def monitor(self):
        while True:
            try:
                await self.refresh()
                running = {item[1] for item in self.queue.get("queue_running", [])}
                pending = {item[1] for item in self.queue.get("queue_pending", [])}
                for job in list(self.jobs.values()):
                    if job["status"] not in ACTIVE:
                        continue
                    prompt_id = job["prompt_id"]
                    history = (await self.comfy("/history/" + prompt_id)).get(prompt_id)
                    previous = job["status"]
                    if history:
                        state = history.get("status", {})
                        messages = state.get("messages", [])
                        errors = [m[1] for m in messages if m[0] in ("execution_error", "execution_interrupted")]
                        job["status"] = "completed" if state.get("completed") else "failed"
                        if errors:
                            job["status"] = "cancelled" if any(m[0] == "execution_interrupted" for m in messages) else "failed"
                            job["error"] = errors[-1].get("exception_message", "Execution interrupted")
                        job["outputs"] = []
                        for output in history.get("outputs", {}).values():
                            for group in output.values():
                                if isinstance(group, list):
                                    for file in group:
                                        if isinstance(file, dict) and str(file.get("filename", "")).lower().endswith((".mp4", ".webm", ".mov", ".mkv")):
                                            job["outputs"].append(file)
                        if job["status"] == "completed" and not job["outputs"]:
                            job["status"], job["error"] = "failed", "ComfyUI finished without a video output."
                        finish_timing(job, messages)
                        if job["status"] == "completed" and job["config"].get("output_folder"):
                            await self.export_job(job)
                        if job["config"]["evict_after"]:
                            self.evict_pending = True
                    elif prompt_id in running:
                        job["status"] = "cancelling" if previous == "cancelling" else "running"
                        job.setdefault("started", time.time())
                    elif prompt_id in pending:
                        job["status"] = "queued"
                    elif time.time() - job["created"] > 20:
                        job["status"] = "cancelled" if previous == "cancelling" else "lost"
                        job["error"] = ("Cancelled before execution." if previous == "cancelling" else
                                        "No longer in ComfyUI's queue or history. It may have restarted. This take was not resubmitted.")
                        job["finished"] = time.time()
                    if previous != job["status"] or history:
                        self.save_job(job)
                if self.evict_pending and not running and not pending:
                    await self.comfy("/free", {"unload_models": True, "free_memory": True})
                    self.last_eviction, self.evict_pending = time.time(), False
            except (OSError, ValueError, asyncio.TimeoutError) as exc:
                self.online, self.error = False, str(exc)[:500]
            except Exception as exc:
                self.online, self.error = False, f"ComfyUI unavailable: {exc}"
            await asyncio.sleep(3)

    async def events(self):
        while True:
            try:
                async with self.session.ws_connect(self.base + "/ws?clientId=" + self.client_id, heartbeat=20) as ws:
                    self.ws_online = True
                    async for message in ws:
                        if message.type != WSMsgType.TEXT:
                            continue
                        event = json.loads(message.data)
                        data = event.get("data", {})
                        pid = data.get("prompt_id")
                        if pid and event["type"] in ("executing", "progress", "execution_start", "execution_cached"):
                            current = self.progress.setdefault(pid, {})
                            if event["type"] == "executing":
                                current.clear()
                                current["node"] = data.get("node")
                            elif event["type"] == "progress":
                                current.update(value=data.get("value"), max=data.get("max"), node=data.get("node", current.get("node")))
            except Exception:
                pass
            self.ws_online = False
            await asyncio.sleep(3)

    async def startup(self, app):
        self.session = ClientSession()
        if self.dependencies:
            await asyncio.to_thread(self.dependencies.validate, True)
            self.setup.apply()
        self.tasks = [asyncio.create_task(self.monitor()), asyncio.create_task(self.events())]
        yield
        if self.setup:
            await self.setup.close()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.session.close()

    async def status(self, request):
        jobs = []
        for job in sorted(self.jobs.values(), key=lambda j: j["created"], reverse=True):
            row = {k: v for k, v in job.items() if k not in ("workflow", "references")}
            row["progress"] = self.progress.get(job["prompt_id"], {})
            jobs.append(row)
        return web.json_response({"workbench": "h3-motionbench", "data_dir": str(self.data.resolve()),
                                  "initialized": not self.dependencies or bool(self.dependencies.rows),
                                  "dependencies": self.dependencies.snapshot() if self.dependencies else None, "online": self.online, "error": self.error, "stats": self.stats,
                                  "queue": {k: len(v) for k, v in self.queue.items()}, "jobs": jobs,
                                  "websocket": self.ws_online, "last_eviction": self.last_eviction,
                                  "evict_pending": self.evict_pending, "comfy_url": self.base})

    async def capabilities(self, request):
        if request.query.get("refresh"):
            self.info = {}
        try:
            await self.refresh()
        except (OSError, ValueError, asyncio.TimeoutError):
            if not self.dependencies:
                raise
            self.online = False
            self.info = {}
        mapping = {"model": ("UnetLoaderGGUF", "unet_name"), "encoder": ("CLIPLoader", "clip_name"),
                   "video_vae": ("VAELoader", "vae_name"), "audio_vae": ("VAELoader", "vae_name"),
                   "lora": ("LoraLoaderModelOnly", "lora_name"), "sampler": ("KSamplerSelect", "sampler_name"),
                   "scheduler": ("BasicScheduler", "scheduler")}
        options = {key: choices(self.info, *value) for key, value in mapping.items()}
        options["model"] = list(dict.fromkeys(v for v in options["model"] + choices(self.info, "UNETLoader", "unet_name") if model_architecture(v)))
        options["encoder"] = [v for v in options["encoder"] if "minimax" in v.lower()]
        options["video_vae"] = [v for v in options["video_vae"] if "h3" in v.lower() and "video" in v.lower()]
        options["audio_vae"] = [v for v in options["audio_vae"] if "h3" in v.lower() and "audio" in v.lower()]
        architectures = {key: {**value, "available": value["node"] in self.info} for key, value in ARCHITECTURES.items()}
        models = {name: {"architecture": model_architecture(name), "loader": model_loader(name)[0]} for name in options["model"]}
        presets = [{**preset, "available": preset["settings"]["model"] in models and architectures[preset["settings"]["architecture"]]["available"]} for preset in PRESETS]
        defaults = dict(DEFAULTS)
        readiness = self.dependencies.snapshot() if self.dependencies else None
        if readiness:
            for preset in presets:
                preset['available'] = preset['available'] and readiness['presets'][preset['id']]['ready']
            for field in ('encoder', 'video_vae', 'audio_vae'):
                defaults[field] = self.dependencies.path(field).name
            available = next((preset for preset in presets if preset['available']), None)
            if available:
                defaults.update(available['settings'])
        return web.json_response({"options": options, "defaults": defaults, "dependencies": readiness,
                                  "architectures": architectures, "modes": MODES, "models": models, "presets": presets,
                                  "weight_bytes": weight_footprints(self.home, options),
                                  "free_encoder": "H3FreeTextEncoder" in self.info,
                                  "strength": "H3ConditionStrength" in self.info})

    async def references(self, request):
        if request.method == "GET":
            return web.json_response(list(self.refs.values()))
        reader = await request.multipart()
        field = await reader.next()
        if field is None or field.name != "image":
            raise ValueError("Upload an image.")
        content = bytearray()
        while chunk := await field.read_chunk():
            content.extend(chunk)
            if len(content) > 20 * 1024 * 1024:
                raise ValueError("Images must be smaller than 20 MB.")
        return web.json_response(self.add_reference(bytes(content), Path(field.filename or "Reference").name))

    async def reference_file(self, request):
        key = request.match_info["id"]
        if key not in self.refs:
            raise web.HTTPNotFound()
        return web.FileResponse(self.data / "refs" / (key + ".png"))

    async def prepare(self, c):
        uploaded, records = {}, {}
        for slot, ref_id, role in input_roles(c):
            ref = self.refs[ref_id]
            content = await asyncio.to_thread(prepare_image, self.data / "refs" / (ref["id"] + ".png"), c, role == "keyframe")
            digest = hashlib.sha256(content).hexdigest()
            import aiohttp
            form = aiohttp.FormData()
            form.add_field("image", content, filename="h3_" + digest[:24] + ".png", content_type="image/png")
            form.add_field("subfolder", "h3-motionbench")
            form.add_field("type", "input")
            async with self.session.post(self.base + "/upload/image", data=form, timeout=ClientTimeout(total=30)) as response:
                result = await response.json()
                if response.status != 200:
                    raise ValueError(f"Reference upload failed: {result}")
            uploaded[slot] = str(Path(result.get("subfolder", "")) / result["name"]).replace("\\", "/")
            records[slot] = {**ref, "prepared_sha256": digest, "comfy_input": uploaded[slot],
                             "role": role, "fit": c["fit"] if role == "keyframe" else "original aspect ratio",
                             "background": c["background"]}
        return uploaded, records

    async def submit(self, request):
        raw = await request.json()
        async with self.lock:
            if len([j for j in self.jobs.values() if j["status"] in ACTIVE]) >= 8:
                raise ValueError("Eight takes are already queued. Wait or remove a queued take.")
            await self.refresh()
            warmup = request.path.endswith("warmup")
            if warmup:
                raw = {**raw, "mode": "text", "seconds": 0.9, "width": 256, "height": 256,
                       "steps": 1, "prompt": "A still gray stone on a plain background.",
                       "sound": "Quiet room tone.", "music": "No music.", "name": "Model warm-up",
                       "evict_after": False, "random_seed": False}
            c = validate(raw, self.info, self.refs)
            if self.dependencies:
                errors = self.dependencies.generation_errors(c, self.info)
                if self.setup.installing():
                    errors.append('Dependency installation is running.')
                if errors:
                    raise ValueError('Open Settings to resolve dependencies:\n' + '\n'.join(errors))
            destination = str(local_folder(c["output_folder"] or self.output_settings["default"]))
            if destination not in self.output_settings["folders"]:
                raise ValueError("Choose a saved output folder, or add it in Local Engine first.")
            await asyncio.to_thread(writable_folder, Path(destination))
            c["output_folder"] = destination
            uploaded, refs = await self.prepare(c)
            key = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
            graph = build_graph(c, uploaded, "video/H3_MotionBench/" + key, self.info)
            result = await self.comfy("/prompt", {"prompt": graph, "client_id": self.client_id})
            if result.get("node_errors"):
                raise ValueError(json.dumps(result["node_errors"]))
            job = {"id": key, "prompt_id": result["prompt_id"], "created": time.time(), "status": "queued",
                   "kind": "warmup" if warmup else "take", "config": c, "workflow": graph,
                   "references": refs, "outputs": [], "comfy_home": str(self.home), "comfy_version": self.stats.get("system", {}).get("comfyui_version")}
            self.jobs[key] = job
            self.save_job(job)
            return web.json_response(job)

    async def cancel(self, request):
        job = self.jobs.get(request.match_info["id"])
        if not job:
            raise web.HTTPNotFound()
        result = await self.comfy("/api/jobs/" + job["prompt_id"] + "/cancel", {})
        if result.get("cancelled"):
            job["status"] = "cancelling"
            self.save_job(job)
        return web.json_response(result)

    async def evict(self, request):
        async with self.lock:
            queue = await self.comfy("/queue")
            if queue.get("queue_running") or queue.get("queue_pending"):
                raise ValueError("ComfyUI has active or queued work. Wait, or cancel your takes first. Eviction affects all loaded models.")
            await self.comfy("/free", {"unload_models": True, "free_memory": True})
            self.last_eviction = time.time()
            return web.json_response({"message": "Model unload and cache release requested. ComfyUI applies this asynchronously."})

    async def start_comfy(self, request):
        if self.dependencies:
            await asyncio.to_thread(self.dependencies.validate)
            missing = [self.dependencies.rows[key]['label'] for key in ('comfy', 'comfy_python') if not self.dependencies.rows[key]['valid']]
            if missing or not self.dependencies.hardware['valid']:
                raise ValueError('Open Settings before starting ComfyUI: ' + ', '.join(missing or [self.dependencies.hardware['message']]))
            if self.setup.installing():
                raise ValueError('Wait for dependency installation to finish.')
            if self.dependencies.restart_required and self.online:
                raise ValueError('Use Restart ComfyUI in Settings to apply the changed paths.')
        try:
            await self.refresh()
            return web.json_response({"message": "ComfyUI is already running."})
        except Exception:
            pass
        if self.process and self.process.poll() is None:
            return web.json_response({"message": "ComfyUI is starting; watch the service log."})
        python = self.dependencies.path("comfy_python") if self.dependencies else self.home / "python_embeded/python.exe"
        if not python.is_file() or not (self.home / "ComfyUI/main.py").is_file():
            raise ValueError("Portable ComfyUI not found. Launch this dashboard with --comfy-home PATH.")
        extra_args = []
        if self.dependencies:
            extra_args = ['--extra-model-paths-config', str(self.dependencies.extra_paths())]
            self.dependencies.restart_required = False
        self.process_base = self.base
        with (self.data / "comfy-service.log").open("ab") as log:
            self.process = subprocess.Popen([str(python), "-s", "ComfyUI/main.py", "--windows-standalone-build",
                                             "--listen", "127.0.0.1", "--port", str(self.comfy_port), *extra_args],
                                            cwd=self.home, stdout=log, stderr=log,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return web.json_response({"message": "Starting ComfyUI. Model weights load when a job executes."})

    async def job_file(self, request):
        job = self.jobs.get(request.match_info["id"])
        if not job:
            raise web.HTTPNotFound()
        kind = request.match_info["kind"]
        if kind in ("record", "workflow"):
            return web.json_response(job if kind == "record" else job["workflow"],
                                     headers={"Content-Disposition": f'attachment; filename="{job["id"]}-{kind}.json"'})
        if kind != "video":
            raise web.HTTPNotFound()
        index = int(request.query.get("index", 0))
        if index < 0 or index >= len(job["outputs"]):
            raise web.HTTPNotFound()
        path = self.video_path(job, index)
        headers = {"Content-Disposition": f'attachment; filename="{path.name}"'} if "download" in request.query else {}
        return web.FileResponse(path, headers=headers)

    async def log(self, request):
        lines = []
        for name in ("comfy-service.log", "comfy.stderr.log", "comfy.stdout.log"):
            path = self.data / name
            if path.exists():
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - 10000))
                    lines.append(stream.read().decode("utf-8", "replace"))
        return web.json_response({"log": "\n".join(lines)[-16000:] or "No service log yet."})


def make_app(workbench):
    @web.middleware
    async def boundary(request, handler):
        allowed = {f"127.0.0.1:{workbench.port}", f"localhost:{workbench.port}"}
        if request.host not in allowed:
            return web.json_response({"error": "Loopback Host required."}, status=403)
        origin = request.headers.get("Origin")
        if origin and origin not in {"http://" + host for host in allowed}:
            return web.json_response({"error": "Cross-origin requests are not allowed."}, status=403)
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:
            return web.json_response({"error": f"ComfyUI request failed: {exc}"}, status=502)

    app = web.Application(middlewares=[boundary], client_max_size=21 * 1024**2)
    app.cleanup_ctx.append(workbench.startup)
    workbench.assembly.install(app)
    if workbench.setup:
        workbench.setup.install(app)
    app.router.add_get("/api/output-folders", workbench.output_folders)
    app.router.add_post("/api/output-folders", workbench.output_folders)
    app.router.add_get("/api/browse-folders", workbench.browse_folders)
    app.router.add_post("/api/jobs/{id}/export", workbench.retry_export)
    app.router.add_post("/api/jobs/{id}/{endpoint:first|last}-frame", workbench.video_frame)
    app.router.add_post("/api/jobs/{id}/rename", workbench.rename)
    app.router.add_delete("/api/jobs/trash", workbench.empty_trash)
    app.router.add_delete("/api/jobs/{id}", workbench.discard)
    app.router.add_get("/api/status", workbench.status)
    app.router.add_get("/api/capabilities", workbench.capabilities)
    app.router.add_get("/api/references", workbench.references)
    app.router.add_post("/api/references", workbench.references)
    app.router.add_get("/api/references/{id}/image", workbench.reference_file)
    app.router.add_post("/api/generate", workbench.submit)
    app.router.add_post("/api/warmup", workbench.submit)
    app.router.add_post("/api/evict", workbench.evict)
    app.router.add_post("/api/start", workbench.start_comfy)
    app.router.add_post("/api/jobs/{id}/cancel", workbench.cancel)
    app.router.add_get("/api/jobs/{id}/{kind}", workbench.job_file)
    app.router.add_get("/api/log", workbench.log)

    async def static(request):
        name = request.match_info.get("name", "index.html")
        if name not in ("index.html", "app.js", "style.css", "assemble.html", "assemble.js", "assemble.css", "settings.html", "settings.js", "settings.css"):
            raise web.HTTPNotFound()
        return web.FileResponse(HERE / name, headers={"Cache-Control": "no-cache"})

    app.router.add_get("/", static)
    app.router.add_get("/{name}", static)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--comfy-port", type=int)
    parser.add_argument("--comfy-home", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    dependencies = Dependencies(HERE, args.config, args.comfy_home, args.comfy_port)
    bench = Workbench(dependencies.path('comfy'), args.data, args.port, dependencies.port, dependencies)
    web.run_app(make_app(bench), host="127.0.0.1", port=args.port)
