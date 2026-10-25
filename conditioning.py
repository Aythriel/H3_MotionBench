"""H3 architecture capabilities, input roles and checkpoint selection."""
from pathlib import PurePosixPath
import re

B25 = "minimax_h3_hybrid_fl2va_ref2va_b25-49-Q4_0.gguf"
B20 = "minimax_h3_hybrid_fl2va_ref2va_b20-49-int8.safetensors"
FL2VA = "MiniMax-H3-fl2va-curve-Q4_0.gguf"
MODES = {
    "first_reference": {"label": "First frame + character references", "frames": ["first"], "references": True},
    "reference": {"label": "Character references only", "frames": [], "references": True},
    "both_reference": {"label": "First + last frame + character references", "frames": ["first", "last"], "references": True},
    "last_reference": {"label": "Last frame + character references", "frames": ["last"], "references": True},
    "first": {"label": "First frame only", "frames": ["first"], "references": False},
    "both": {"label": "First + last frame", "frames": ["first", "last"], "references": False},
    "last": {"label": "Last frame only", "frames": ["last"], "references": False},
    "text": {"label": "Text only", "frames": [], "references": False},
}
ARCHITECTURES = {
    "hybrid": {"label": "H3 Hybrid · frames + references", "node": "MiniMaxH3ImageAndReferenceToVideo",
               "modes": list(MODES), "default_mode": "first_reference",
               "description": "Continue the pose from a frame and keep original artwork as separate character guidance."},
    "fl2va": {"label": "H3 FL2VA · frame anchors", "node": "MiniMaxH3ImageToVideo",
              "modes": ["first", "both", "last", "text"], "default_mode": "first",
              "description": "First and last images anchor the shot. This architecture has no independent character references."},
    "ref2va": {"label": "H3 REF2VA · references", "node": "MiniMaxH3ReferenceToVideo",
               "modes": ["reference", "text"], "default_mode": "reference",
               "description": "Images guide character appearance without occupying a fixed frame."},
}
PRESETS = [
    {"id": "hybrid_balanced", "label": "Hybrid balanced · b25 Q4 · default", "settings": {
        "architecture": "hybrid", "model": B25, "mode": "first_reference"}},
    {"id": "hybrid_reference", "label": "Hybrid stronger references · b20 INT8", "settings": {
        "architecture": "hybrid", "model": B20, "mode": "first_reference"}},
    {"id": "fl2va", "label": "Original FL2VA · Q4", "settings": {
        "architecture": "fl2va", "model": FL2VA, "mode": "first"}},
]
BASE_SAMPLING = {"lora": "", "steps": 20, "sampler": "res_multistep", "scheduler": "simple", "denoise": 1.0}
for preset in PRESETS:
    preset["settings"].update(BASE_SAMPLING, ref_image_size="match")


def model_architecture(name):
    name = PurePosixPath(str(name).replace("\\", "/")).name.lower()
    if "h3" not in name or not name.endswith((".gguf", ".safetensors")):
        return None
    if "hybrid" in name and "fl2va" in name and "ref2va" in name:
        return "hybrid"
    if "ref2va" in name and "fl2va" not in name:
        return "ref2va"
    if "fl2va" in name and "ref2va" not in name:
        return "fl2va"
    return None


def model_loader(name):
    return ("UnetLoaderGGUF", "unet_name") if name.lower().endswith(".gguf") else ("UNETLoader", "unet_name")


def mode_spec(config):
    architecture = ARCHITECTURES.get(config["architecture"])
    if not architecture or config["mode"] not in architecture["modes"]:
        raise ValueError("The selected conditioning mode is not supported by this architecture.")
    return MODES[config["mode"]]


def input_roles(config):
    spec = mode_spec(config)
    result = [(slot, config[slot], "keyframe") for slot in spec["frames"]]
    if spec["references"]:
        result.extend((f"identity_{index}", ref, "identity")
                      for index, ref in enumerate(config["identity_images"], 1))
    return result


def validate_conditioning(config, raw, info, references):
    architecture = raw.get("architecture") or model_architecture(config["model"])
    if architecture not in ARCHITECTURES:
        raise ValueError("Choose a supported H3 architecture.")
    config["architecture"] = architecture
    if model_architecture(config["model"]) != architecture:
        raise ValueError("The selected checkpoint does not match its architecture. Choose a matching preset or model.")
    spec = mode_spec(config)
    if ARCHITECTURES[architecture]["node"] not in info:
        raise ValueError(f"Missing {ARCHITECTURES[architecture]['node']}. Install its node and restart ComfyUI.")
    if config["ref_image_size"] not in ("match", "max"):
        raise ValueError("Choose match or max for character reference detail.")
    ids = config["identity_images"]
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        raise ValueError("Character references must be a list of image IDs.")
    if spec["references"]:
        if not 1 <= len(ids) <= 9 or len(set(ids)) != len(ids):
            raise ValueError("Choose one to nine distinct character references.")
        if any(ref not in references for ref in ids):
            raise ValueError("A character reference is unavailable. Select or upload it again.")
    else:
        config["identity_images"] = []
    for slot in ("first", "last"):
        if slot in spec["frames"]:
            if config[slot] not in references:
                raise ValueError(f"Select the {slot} frame.")
        else:
            config[slot] = ""
    if architecture != "fl2va" and config["lora"] and "fl2v" in config["lora"].lower():
        raise ValueError("The installed FL2VA turbo LoRA is not approved for this architecture. Use base sampling.")
    if architecture == "hybrid":
        schema = info[ARCHITECTURES[architecture]["node"]].get("input", {})
        if "first_frame_first" not in {**schema.get("required", {}), **schema.get("optional", {})}:
            raise ValueError("The hybrid node needs the first-frame numbering update. Update its local source and restart ComfyUI.")
    if raw.get("settings_version") == 2 and spec["references"] and "first" in spec["frames"]:
        count = len(config["identity_images"])
        def renumber(match):
            number = int(match[1])
            return f"<Picture {1 if number == count + 1 else number + 1}>" if number <= count + 1 else match[0]
        for field in ("prompt", "sound", "music"):
            config[field] = re.sub(r"<Picture ([1-9][0-9]*)>", renumber, config[field])
    config["settings_version"] = 3
