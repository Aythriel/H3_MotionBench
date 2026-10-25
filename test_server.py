import asyncio
import io
import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

import av

from PIL import Image
from aiohttp.test_utils import AioHTTPTestCase

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conditioning import B25, B20, FL2VA
from server import DEFAULTS, Workbench, build_graph, finish_timing, frame_count, make_app, prepare_image, validate, weight_footprints


def node_catalogue():
    mapping = {
        "UnetLoaderGGUF": {"unet_name": [[B25, FL2VA, "MiniMax-H3-ref2va-Q4_0.gguf"]]},
        "UNETLoader": {"unet_name": [[B20]]},
        "CLIPLoader": {"clip_name": [[DEFAULTS["encoder"]]]},
        "VAELoader": {"vae_name": [[DEFAULTS["video_vae"], DEFAULTS["audio_vae"]]]},
        "KSamplerSelect": {"sampler_name": [["res_multistep", "euler"]]},
        "BasicScheduler": {"scheduler": [["simple", "beta"]]},
        "LoraLoaderModelOnly": {"lora_name": [["turbo.safetensors"]]},
    }
    result = {key: {"input": {"required": value}} for key, value in mapping.items()}
    for name in ["MiniMaxH3ImageAndReferenceToVideo", "MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo", "LoadImage", "RandomNoise", "BasicGuider", "SamplerCustomAdvanced",
                 "VAEDecode", "VAEDecodeTiled", "VAEDecodeAudio", "CreateVideo", "SaveVideo",
                 "H3ConditionStrength", "H3FreeTextEncoder"]:
        result.setdefault(name, {})
    result["MiniMaxH3ImageAndReferenceToVideo"]["input"] = {"optional": {"first_frame_first": ["BOOLEAN", {"default": False}]}}
    return result


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.info = node_catalogue()

    def config(self, **overrides):
        return validate({**DEFAULTS, "mode": "text", **overrides}, self.info, {"ref": {}})

    def test_weight_footprints_measure_files_and_keep_unknown_sizes(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            models = home / "ComfyUI/models/diffusion_models"
            models.mkdir(parents=True)
            (models / "test.gguf").write_bytes(b"weights")
            outside = home / "secret.gguf"
            outside.write_bytes(b"private")
            result = weight_footprints(home, {"model": ["test.gguf", "missing.gguf", "../../../secret.gguf"]})
            self.assertEqual(result["model"]["test.gguf"], 7)
            self.assertIsNone(result["model"]["missing.gguf"])
            self.assertIsNone(result["model"]["../../../secret.gguf"])

    def test_frame_grid_and_resolved_duration(self):
        self.assertEqual([frame_count(s) for s in [0.2, 0.9, 2, 5, 15]], [5, 22, 56, 124, 362])
        self.assertEqual(self.config(seconds=2)["actual_seconds"], 56/24)

    def test_reject_invalid_controls_and_missing_references(self):
        for bad in [{"width": 609}, {"width": 1536, "height": 1536}, {"seed": 1.5}, {"seconds": float("nan")},
                    {"mode": "both", "first": "ref"}, {"model": "uninstalled.gguf"}, {"free_encoder": "yes"},
                    {"lora": "missing"}, {"steps": 0}, {"seed": 2**53}, {"background": "red"}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.config(**bad)

    def test_both_anchors_and_audio_reach_output(self):
        c = self.config(mode="both", first="ref", last="ref", lora="turbo.safetensors", tiled=True)
        graph = build_graph(c, {"first": "a.png", "last": "b.png"}, "test", self.info)
        self.assertEqual(graph["condition"]["inputs"]["first_frame"], ["first", 0])
        self.assertEqual(graph["condition"]["inputs"]["last_frame"], ["last", 0])
        self.assertEqual(graph["guider"]["inputs"]["model"], ["lora", 0])
        self.assertEqual(graph["schedule"]["inputs"]["model"], ["lora", 0])
        self.assertEqual(graph["mux"]["inputs"]["audio"], ["decode_audio", 0])
        self.assertEqual(graph["decode_audio"]["inputs"]["samples"], ["sample", 0])
        self.assertEqual(graph["save"]["inputs"]["video"], ["mux", 0])
        self.assertEqual(graph["decode"]["class_type"], "VAEDecodeTiled")
        prompt = json.loads(graph["condition"]["inputs"]["prompt"])
        self.assertEqual(prompt["overall_soundscape"], c["sound"])
        self.assertEqual(graph["guider"]["inputs"]["conditioning"], ["free_encoder", 0])

    def test_text_mode_omits_anchors_and_strength(self):
        graph = build_graph(self.config(), {}, "test", self.info)
        self.assertNotIn("strength", graph)
        self.assertNotIn("first_frame", graph["condition"]["inputs"])

    def test_image_contain_preserves_geometry_and_composites_alpha(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.png"
            image = Image.new("RGBA", (200, 100), (255, 0, 0, 255))
            image.putpixel((0, 0), (0, 0, 0, 0))
            image.save(path)
            prepared = Image.open(io.BytesIO(prepare_image(path, self.config(width=256, height=256))))
            self.assertEqual(prepared.size, (256, 256))
            self.assertEqual(prepared.mode, "RGB")
            self.assertEqual(prepared.getpixel((128, 0)), (0, 255, 0))
            self.assertEqual(prepared.getpixel((128, 128)), (255, 0, 0))


class TimingTests(unittest.TestCase):
    def test_engine_timestamps_exclude_queue_and_recovery_delay(self):
        job = {"created": 100, "started": 125}
        finish_timing(job, [["execution_start", {"timestamp": 120000}],
                            ["execution_success", {"timestamp": 185750}]])
        self.assertEqual(job["started"], 120)
        self.assertEqual(job["finished"] - job["started"], 65.75)

    def test_missing_engine_timestamps_keep_observed_start(self):
        job = {"created": 100, "started": 125}
        with patch("server.time.time", return_value=200):
            finish_timing(job, [["execution_start", {}], ["execution_success", {"timestamp": None}]])
        self.assertEqual(job, {"created": 100, "started": 125, "finished": 200})

    def test_without_a_start_keeps_submission_time_fallback_explicit(self):
        job = {"created": 100}
        with patch("server.time.time", return_value=200):
            finish_timing(job, [])
        self.assertNotIn("started", job)
        self.assertEqual(job["finished"], 200)


class ApiTests(AioHTTPTestCase):
    async def get_application(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bench = Workbench(Path(self.temp.name), Path(self.temp.name)/"state", 8788, 8188)
        self.calls = []

        async def comfy(route, payload=None):
            self.calls.append((route, payload))
            if route == "/queue":
                return {"queue_running": [], "queue_pending": []}
            if route == "/system_stats":
                return {"system": {"comfyui_version": "test"}}
            if route == "/object_info":
                return node_catalogue()
            if route == "/prompt":
                return {"prompt_id": "00000000-0000-0000-0000-000000000001"}
            return {"cancelled": True}

        self.bench.comfy = comfy
        app = make_app(self.bench)
        app.cleanup_ctx.clear()
        return app

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.temp.cleanup()

    async def post(self, path, payload, **headers):
        return await self.client.post(path, json=payload, headers={"Host": "127.0.0.1:8788", **headers})

    async def test_status_identifies_standalone_dashboard(self):
        response = await self.client.get("/api/status", headers={"Host": "127.0.0.1:8788"})
        self.assertEqual(response.status, 200)
        status = await response.json()
        self.assertEqual(status["workbench"], "h3-motionbench")
        self.assertEqual(status["data_dir"], str(self.bench.data.resolve()))

    async def test_monitor_persists_recovered_execution_timing(self):
        response = await self.post("/api/generate", {**DEFAULTS, "mode": "text"})
        submitted = await response.json()
        job = self.bench.jobs[submitted["id"]]
        job["config"]["output_folder"] = ""
        original_comfy = self.bench.comfy

        async def recovered(route, payload=None):
            if route.startswith("/history/"):
                return {job["prompt_id"]: {"status": {"completed": True, "messages": [
                    ["execution_start", {"timestamp": 120000}],
                    ["execution_success", {"timestamp": 185750}]]},
                    "outputs": {"save": {"videos": [{"filename": "take.mp4"}]}}}}
            return await original_comfy(route, payload)

        self.bench.comfy = recovered
        with patch("server.asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.bench.monitor()
        restored = Workbench(self.bench.home, self.bench.data, 8788, 8188)
        self.assertEqual(restored.jobs[job["id"]]["status"], "completed")
        self.assertEqual(restored.jobs[job["id"]]["started"], 120)
        self.assertEqual(restored.jobs[job["id"]]["finished"], 185.75)

    async def test_reject_cross_origin_mutation(self):
        response = await self.post("/api/evict", {}, Origin="https://untrusted.example")
        self.assertEqual(response.status, 403)
        self.assertEqual(self.calls, [])

    async def test_invalid_submission_does_not_enqueue(self):
        response = await self.post("/api/generate", {**DEFAULTS, "mode": "text", "width": 777})
        self.assertEqual(response.status, 400)
        self.assertFalse(any(route == "/prompt" for route, _ in self.calls))

    async def test_submission_persists_exact_graph_and_seed(self):
        response = await self.post("/api/generate", {**DEFAULTS, "mode": "text", "seed": 123})
        self.assertEqual(response.status, 200)
        job = await response.json()
        saved = json.loads((self.bench.data/"jobs"/(job["id"]+".json")).read_text())
        self.assertEqual(job, saved)
        self.assertEqual(saved["config"]["seed"], 123)
        submitted = next(payload for route, payload in self.calls if route == "/prompt")
        self.assertEqual(submitted["prompt"], saved["workflow"])
        restored = Workbench(self.bench.home, self.bench.data, 8788, 8188)
        self.assertEqual(restored.jobs[job["id"]], job)

    async def test_default_folder_persists_and_does_not_move_queued_takes(self):
        selected = str(Path(self.temp.name) / "Chosen output")
        response = await self.post("/api/output-folders", {"path": selected, "action": "add"})
        self.assertEqual(response.status, 200)
        before = self.bench.output_settings["default"]
        response = await self.post("/api/generate", {**DEFAULTS, "mode": "text", "output_folder": selected})
        first = await response.json()
        self.assertEqual(first["config"]["output_folder"], selected)
        await self.post("/api/output-folders", {"path": selected, "action": "default"})
        restored = Workbench(self.bench.home, self.bench.data, 8788, 8188)
        self.assertEqual(restored.output_settings["default"], selected)
        response = await self.post("/api/generate", {**DEFAULTS, "mode": "text"})
        second = await response.json()
        self.assertEqual(second["config"]["output_folder"], selected)
        await self.post("/api/output-folders", {"path": before, "action": "default"})
        self.assertEqual(self.bench.jobs[first["id"]]["config"]["output_folder"], selected)

    async def test_unregistered_or_unwritable_destination_rejects_before_generation(self):
        response = await self.post("/api/generate", {**DEFAULTS, "mode": "text", "output_folder": str(Path(self.temp.name)/"unknown")})
        self.assertEqual(response.status, 400)
        self.assertFalse(any(route == "/prompt" for route, _ in self.calls))
        existing = Path(self.temp.name)/"file.txt"
        existing.write_text("keep")
        response = await self.post("/api/output-folders", {"path": str(existing), "action": "add"})
        self.assertEqual(response.status, 400)
        self.assertEqual(existing.read_text(), "keep")
        self.assertNotIn(str(existing), self.bench.output_settings["folders"])
        response = await self.post("/api/output-folders", {"path": "relative/folder", "action": "add"})
        self.assertEqual(response.status, 400)

    async def test_saved_video_record_download_and_retry_without_regeneration(self):
        folder = Path(self.temp.name)/"Chosen output"
        await self.post("/api/output-folders", {"path": str(folder), "action": "add"})
        response = await self.post("/api/generate", {**DEFAULTS, "mode": "text", "output_folder": str(folder)})
        submitted = await response.json()
        job = self.bench.jobs[submitted["id"]]
        source = self.bench.home/"ComfyUI/output/video/test.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"video and audio bytes")
        job.update(status="completed", outputs=[{"filename": "test.mp4", "subfolder": "video", "type": "output"}])
        destination = folder/"test.mp4"
        destination.write_bytes(b"unrelated existing output")
        await self.bench.export_job(job)
        self.assertIn("already exists", job["export_error"])
        self.assertEqual(destination.read_bytes(), b"unrelated existing output")
        destination.unlink()
        calls_before = len(self.calls)
        response = await self.post(f'/api/jobs/{job["id"]}/export', {})
        self.assertEqual(response.status, 200)
        self.assertEqual(len(self.calls), calls_before)
        self.assertEqual(destination.read_bytes(), source.read_bytes())
        record = json.loads((folder/(job["id"]+"-record.json")).read_text())
        self.assertEqual(record["saved_outputs"], [str(destination)])
        self.assertNotIn("export_error", job)
        source.unlink()
        response = await self.client.get(f'/api/jobs/{job["id"]}/video', headers={"Host": "127.0.0.1:8788"})
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), b"video and audio bytes")
        restored = Workbench(self.bench.home, self.bench.data, 8788, 8188)
        self.assertEqual(restored.jobs[job["id"]]["saved_outputs"], [str(destination)])

    async def test_discard_removes_only_the_completed_take_media_and_keeps_settings(self):
        folder = Path(self.temp.name) / "Chosen output"
        await self.post("/api/output-folders", {"path": str(folder), "action": "add"})
        response = await self.post("/api/generate", {**DEFAULTS, "mode": "text", "output_folder": str(folder)})
        submitted = await response.json()
        job = self.bench.jobs[submitted["id"]]
        source = self.bench.home / "ComfyUI/output/video/discard.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"generated media")
        job.update(status="completed", outputs=[{"filename": "discard.mp4", "subfolder": "video", "type": "output"}])
        await self.bench.export_job(job)
        destination = Path(job["saved_outputs"][0])
        record = folder / (job["id"] + "-record.json")
        self.assertTrue(source.is_file() and destination.is_file() and record.is_file())
        response = await self.client.delete(f'/api/jobs/{job["id"]}', headers={"Host": "127.0.0.1:8788"})
        self.assertEqual(response.status, 200)
        self.assertFalse(source.exists() or destination.exists() or record.exists())
        self.assertEqual(job["status"], "discarded")
        self.assertEqual(job["outputs"], [])
        self.assertEqual(job["saved_outputs"], [])
        self.assertEqual(job["config"]["seed"], DEFAULTS["seed"])
        persisted = json.loads((self.bench.data / "jobs" / (job["id"] + ".json")).read_text())
        self.assertEqual(persisted["status"], "discarded")
        self.assertTrue(persisted["deleted_media"])

    async def history_job(self, status):
        response = await self.post("/api/generate", {**DEFAULTS, "mode": "text"})
        submitted = await response.json()
        job = self.bench.jobs[submitted["id"]]
        job["status"] = status
        self.bench.save_job(job)
        return job

    async def test_purge_discarded_removes_record_and_keeps_references_and_reused_paths(self):
        job = await self.history_job("discarded")
        source = self.bench.home / "ComfyUI/output/new-owner.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"different video now at an old deleted path")
        job["deleted_media"] = [str(source)]
        blob = io.BytesIO()
        Image.new("RGB", (64, 64), "blue").save(blob, format="PNG")
        ref = self.bench.add_reference(blob.getvalue(), "Shared reference")
        job["first_frame_reference"] = ref["id"]
        self.bench.save_job(job)
        response = await self.client.delete(f'/api/jobs/{job["id"]}', headers={"Host": "127.0.0.1:8788"})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["removed"], [job["id"]])
        self.assertNotIn(job["id"], self.bench.jobs)
        self.assertFalse((self.bench.data / "jobs" / (job["id"] + ".json")).exists())
        self.assertTrue(source.is_file())
        self.assertTrue((self.bench.data / "refs" / (ref["id"] + ".png")).is_file())
        restored = Workbench(self.bench.home, self.bench.data, 8788, 8188)
        self.assertNotIn(job["id"], restored.jobs)
        self.assertIn(ref["id"], restored.refs)
        response = await self.client.get(f'/api/jobs/{job["id"]}/record', headers={"Host": "127.0.0.1:8788"})
        self.assertEqual(response.status, 404)

    async def test_failed_take_is_deleted_outright_with_or_without_partial_outputs(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                job = await self.history_job("failed")
                if partial:
                    source = self.bench.home / "ComfyUI/output/partial.mp4"
                    source.parent.mkdir(parents=True, exist_ok=True)
                    source.write_bytes(b"partial video")
                    job["outputs"] = [{"filename": source.name, "type": "output"}]
                    await self.bench.export_job(job)
                    saved = Path(job["saved_outputs"][0])
                    record = saved.parent / (job["id"] + "-record.json")
                before = len(self.calls)
                response = await self.client.delete(f'/api/jobs/{job["id"]}', headers={"Host": "127.0.0.1:8788"})
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual(len(self.calls), before)
                self.assertNotIn(job["id"], self.bench.jobs)
                restored = Workbench(self.bench.home, self.bench.data, 8788, 8188)
                self.assertNotIn(job["id"], restored.jobs)
                if partial:
                    self.assertFalse(source.exists() or saved.exists() or record.exists())

    async def test_empty_trash_removes_only_discarded_takes_and_is_idempotent(self):
        trash = [await self.history_job("discarded") for _ in range(2)]
        kept = [await self.history_job(status) for status in ("completed", "cancelled", "failed", "queued")]
        response = await self.client.delete('/api/jobs/trash', headers={"Host": "127.0.0.1:8788", "Origin": "https://untrusted.example"})
        self.assertEqual(response.status, 403)
        self.assertEqual(len(self.bench.jobs), 6)
        response = await self.client.delete('/api/jobs/trash', headers={"Host": "127.0.0.1:8788"})
        self.assertEqual(response.status, 200)
        self.assertCountEqual((await response.json())["removed"], [job["id"] for job in trash])
        restored = Workbench(self.bench.home, self.bench.data, 8788, 8188)
        self.assertCountEqual(restored.jobs, [job["id"] for job in kept])
        response = await self.client.delete('/api/jobs/trash', headers={"Host": "127.0.0.1:8788"})
        self.assertEqual((await response.json())["removed"], [])

    async def test_purge_validates_all_paths_before_removing_any_take(self):
        first = await self.history_job("discarded")
        unsafe = await self.history_job("discarded")
        outside = Path(self.temp.name) / "outside.mp4"
        outside.write_bytes(b"keep")
        unsafe["saved_outputs"] = [str(outside)]
        response = await self.client.delete('/api/jobs/trash', headers={"Host": "127.0.0.1:8788"})
        self.assertEqual(response.status, 400)
        self.assertEqual(outside.read_bytes(), b"keep")
        self.assertTrue((self.bench.data / "jobs" / (first["id"] + ".json")).is_file())
        self.assertIn(first["id"], self.bench.jobs)
        self.assertIn(unsafe["id"], self.bench.jobs)

    async def test_purge_refuses_in_use_take_and_retains_record_on_disk_failure(self):
        job = await self.history_job("failed")
        endpoint = f'/api/jobs/{job["id"]}'
        self.bench.exporting_jobs.add(job["id"])
        response = await self.client.delete(endpoint, headers={"Host": "127.0.0.1:8788"})
        self.assertEqual(response.status, 400)
        self.bench.exporting_jobs.clear()
        with patch.object(self.bench.assembly, "source_in_use", return_value=True):
            response = await self.client.delete(endpoint, headers={"Host": "127.0.0.1:8788"})
            self.assertEqual(response.status, 400)
        with patch.object(Path, "unlink", side_effect=OSError("File is locked")):
            response = await self.client.delete(endpoint, headers={"Host": "127.0.0.1:8788"})
            self.assertEqual(response.status, 400)
        self.assertIn(job["id"], self.bench.jobs)
        self.assertTrue((self.bench.data / "jobs" / (job["id"] + ".json")).is_file())
        response = await self.client.delete(endpoint, headers={"Host": "127.0.0.1:8788"})
        self.assertEqual(response.status, 200)

    async def test_endpoint_frames_are_exact_persistent_and_survive_video_deletion(self):
        cases = [(saved, endpoint, color) for saved in (False, True)
                 for endpoint, color in [("first", (255, 0, 0, 255)), ("last", (0, 255, 0, 255))]]
        for saved_copy, endpoint, expected_color in cases:
            with self.subTest(saved_copy=saved_copy, endpoint=endpoint):
                response = await self.post("/api/generate", {**DEFAULTS, "mode": "text"})
                submitted = await response.json()
                job = self.bench.jobs[submitted["id"]]
                source = self.bench.home / "ComfyUI/output/video/frames.mkv"
                source.parent.mkdir(parents=True, exist_ok=True)
                with av.open(str(source), "w") as container:
                    stream = container.add_stream("ffv1", rate=24)
                    stream.width, stream.height, stream.pix_fmt = 64, 96, "bgr0"
                    for color in [(255, 0, 0), (0, 0, 255), (0, 255, 0)]:
                        frame = av.VideoFrame.from_image(Image.new("RGB", (64, 96), color))
                        for packet in stream.encode(frame):
                            container.mux(packet)
                    for packet in stream.encode():
                        container.mux(packet)
                job.update(status="completed", outputs=[{"filename": source.name, "subfolder": "video", "type": "output"}])
                if saved_copy:
                    await self.bench.export_job(job)
                    source.unlink()
                else:
                    job["config"].pop("output_folder")
                calls_before = len(self.calls)
                response = await self.post(f'/api/jobs/{job["id"]}/{endpoint}-frame', {})
                self.assertEqual(response.status, 200, await response.text())
                ref = await response.json()
                self.assertEqual((ref["width"], ref["height"]), (64, 96))
                self.assertEqual(ref["source"]["job_id"], job["id"])
                self.assertEqual(ref["source"]["kind"], f"video_{endpoint}_frame")
                image_response = await self.client.get(f'/api/references/{ref["id"]}/image', headers={"Host": "127.0.0.1:8788"})
                with Image.open(io.BytesIO(await image_response.read())) as image:
                    self.assertEqual(image.getpixel((32, 48)), expected_color)
                self.assertEqual(len(self.calls), calls_before)
                restored = Workbench(self.bench.home, self.bench.data, 8788, 8188)
                self.assertEqual(restored.jobs[job["id"]][f"{endpoint}_frame_reference"], ref["id"])
                self.assertEqual(restored.refs[ref["id"]], ref)
                self.bench.jobs, self.bench.refs = restored.jobs, restored.refs
                again = await self.post(f'/api/jobs/{job["id"]}/{endpoint}-frame', {})
                self.assertEqual(await again.json(), ref)
                renamed = await self.post(f'/api/jobs/{job["id"]}/rename', {"name": "Renamed take"})
                self.assertEqual(renamed.status, 200)
                self.assertEqual(self.bench.refs[ref["id"]]["name"], f"Renamed take - {endpoint} frame")
                continuation = validate({**DEFAULTS, "mode": "first", "first": ref["id"]}, node_catalogue(), restored.refs)
                self.assertEqual(continuation["first"], ref["id"])
                response = await self.client.delete(f'/api/jobs/{job["id"]}', headers={"Host": "127.0.0.1:8788"})
                self.assertEqual(response.status, 200)
                self.assertTrue((self.bench.data / "refs" / (ref["id"] + ".png")).is_file())

    async def test_first_frame_rejects_unfinished_missing_and_damaged_media(self):
        await self.check_invalid_frame("first")

    async def test_last_frame_rejects_unfinished_missing_and_damaged_media(self):
        await self.check_invalid_frame("last")

    async def check_invalid_frame(self, frame):
        response = await self.post("/api/generate", {**DEFAULTS, "mode": "text"})
        submitted = await response.json()
        job = self.bench.jobs[submitted["id"]]
        endpoint = f'/api/jobs/{job["id"]}/{frame}-frame'
        response = await self.post(endpoint, {})
        self.assertEqual(response.status, 400)
        response = await self.post(endpoint, {}, Origin="https://untrusted.example")
        self.assertEqual(response.status, 403)
        job.update(status="completed", outputs=[{"filename": "damaged.mp4", "subfolder": "video", "type": "output"}])
        response = await self.post(endpoint, {})
        self.assertEqual(response.status, 400)
        source = self.bench.home / "ComfyUI/output/video/damaged.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"invalid video")
        refs_before = dict(self.bench.refs)
        response = await self.post(endpoint, {})
        self.assertEqual(response.status, 400)
        self.assertIn("incomplete or damaged", (await response.json())["error"])
        self.assertEqual(self.bench.refs, refs_before)
        self.assertNotIn(f"{frame}_frame_reference", job)

    async def test_discard_legacy_take_without_output_folder(self):
        for folder_value in [None, ""]:
            with self.subTest(folder_value=folder_value):
                response = await self.post("/api/generate", {**DEFAULTS, "mode": "text"})
                submitted = await response.json()
                job = self.bench.jobs[submitted["id"]]
                if folder_value is None:
                    job["config"].pop("output_folder")
                else:
                    job["config"]["output_folder"] = folder_value
                source = self.bench.home / "ComfyUI/output/video/legacy.mp4"
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"legacy generated media")
                job.update(status="completed", outputs=[{"filename": source.name, "subfolder": "video", "type": "output"}])
                job.pop("saved_outputs", None)
                folder = Path(self.bench.output_settings["default"])
                folder.mkdir(parents=True, exist_ok=True)
                unrelated = folder / source.name
                unrelated.write_bytes(b"unrelated media")
                record = folder / (job["id"] + "-record.json")
                record.write_text("unrelated record")
                self.bench.save_job(job)
                restored = Workbench(self.bench.home, self.bench.data, 8788, 8188)
                self.bench.jobs = restored.jobs
                response = await self.client.delete(f'/api/jobs/{job["id"]}', headers={"Host": "127.0.0.1:8788"})
                self.assertEqual(response.status, 200, await response.text())
                self.assertFalse(source.exists())
                self.assertEqual(unrelated.read_bytes(), b"unrelated media")
                self.assertEqual(record.read_text(), "unrelated record")
                persisted = self.bench.read(self.bench.data / "jobs" / (job["id"] + ".json"), {})
                self.assertEqual(persisted["status"], "discarded")
                self.assertEqual(persisted["config"], job["config"])
                self.assertEqual(persisted["deleted_media"], [str(source.resolve())])

    async def test_discard_rejects_saved_copies_without_recorded_folder(self):
        response = await self.post("/api/generate", {**DEFAULTS, "mode": "text"})
        submitted = await response.json()
        job = self.bench.jobs[submitted["id"]]
        job["config"].pop("output_folder")
        source = self.bench.home / "ComfyUI/output/video/legacy.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"keep source")
        outside = Path(self.temp.name) / "outside.mp4"
        outside.write_bytes(b"keep copy")
        job.update(status="completed", outputs=[{"filename": source.name, "subfolder": "video", "type": "output"}], saved_outputs=[str(outside)])
        response = await self.client.delete(f'/api/jobs/{job["id"]}', headers={"Host": "127.0.0.1:8788"})
        self.assertEqual(response.status, 400)
        self.assertIn("no recorded output folder", (await response.json())["error"])
        self.assertEqual(source.read_bytes(), b"keep source")
        self.assertEqual(outside.read_bytes(), b"keep copy")
        self.assertEqual(job["status"], "completed")

    async def test_discard_rejects_non_completed_take_and_unsafe_saved_path(self):
        response = await self.post("/api/generate", {**DEFAULTS, "mode": "text"})
        job = await response.json()
        response = await self.client.delete(f'/api/jobs/{job["id"]}', headers={"Host": "127.0.0.1:8788"})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.bench.jobs[job["id"]]["status"], "queued")
        folder = Path(self.temp.name) / "Chosen output"
        await self.post("/api/output-folders", {"path": str(folder), "action": "add"})
        self.bench.jobs[job["id"]].update(status="completed", config={**self.bench.jobs[job["id"]]["config"], "output_folder": str(folder)}, saved_outputs=[str(Path(self.temp.name) / "outside.mp4")])
        outside = Path(self.temp.name) / "outside.mp4"
        outside.write_bytes(b"do not delete")
        response = await self.client.delete(f'/api/jobs/{job["id"]}', headers={"Host": "127.0.0.1:8788"})
        self.assertEqual(response.status, 400)
        self.assertEqual(outside.read_bytes(), b"do not delete")

    async def test_eviction_refuses_foreign_queue(self):
        async def busy(route, payload=None):
            self.calls.append((route, payload))
            return {"queue_running": [[0, "foreign"]], "queue_pending": []}
        self.bench.comfy = busy
        response = await self.post("/api/evict", {})
        self.assertEqual(response.status, 400)
        self.assertFalse(any(route == "/free" for route, _ in self.calls))

    async def test_cancel_targets_only_owned_prompt(self):
        response = await self.post("/api/generate", {**DEFAULTS, "mode": "text"})
        job = await response.json()
        response = await self.post(f'/api/jobs/{job["id"]}/cancel', {})
        self.assertEqual(response.status, 200)
        self.assertIn((f'/api/jobs/{job["prompt_id"]}/cancel', {}), self.calls)
        self.assertFalse(any(route == "/interrupt" for route, _ in self.calls))


if __name__ == "__main__":
    unittest.main()
