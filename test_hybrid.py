import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from PIL import Image
from server import DEFAULTS, validate, build_graph, prepare_image
from conditioning import B25, B20, FL2VA, input_roles
from test_server import node_catalogue


class HybridTests(unittest.TestCase):
    def setUp(self):
        self.info = node_catalogue()

    def config(self, **changes):
        return validate({**DEFAULTS, "mode": "text", **changes}, self.info, {"ref": {}, "face": {}})

    def test_hybrid_keeps_identity_separate_from_timeline(self):
        c = self.config(mode="first_reference", first="ref", last="unused", identity_images=["face"])
        graph = build_graph(c, {"first": "pose.png", "identity_1": "art.png"}, "test", self.info)
        inputs = graph["condition"]["inputs"]
        self.assertEqual(graph["condition"]["class_type"], "MiniMaxH3ImageAndReferenceToVideo")
        self.assertEqual(inputs["first_frame"], ["first", 0])
        self.assertEqual(inputs["ref_images.ref_image_1"], ["identity_1", 0])
        self.assertNotIn("last_frame", inputs)
        self.assertEqual(c["last"], "")
        self.assertEqual(input_roles(c), [("first", "ref", "keyframe"), ("identity_1", "face", "identity")])
        self.assertIn("<Picture 2>", json.loads(inputs["prompt"])["subject_definitions"])
        self.assertNotIn("<Picture 1>", json.loads(inputs["prompt"])["subject_definitions"])
        self.assertTrue(inputs["first_frame_first"])
        self.assertEqual(inputs["audio_vae"], ["audio_vae", 0])

    def test_lead_in_uses_last_anchor_and_separate_character_references(self):
        c = self.config(mode="last_reference", first="unused", last="ref", identity_images=["face"])
        graph = build_graph(c, {"last": "take-first-frame.png", "identity_1": "art.png"}, "lead-in", self.info)
        inputs = graph["condition"]["inputs"]
        self.assertNotIn("first_frame", inputs)
        self.assertEqual(c["first"], "")
        self.assertEqual(inputs["last_frame"], ["last", 0])
        self.assertEqual(inputs["ref_images.ref_image_1"], ["identity_1", 0])
        self.assertIn("<Picture 1>", json.loads(inputs["prompt"])["subject_definitions"])
        self.assertEqual(input_roles(c), [("last", "ref", "keyframe"), ("identity_1", "face", "identity")])

    def test_reference_order_survives_roundtrip_and_hidden_inputs_are_omitted(self):
        c = self.config(mode="reference", first="missing", identity_images=["ref", "face"])
        self.assertEqual(c, validate(json.loads(json.dumps(c)), self.info, {"ref": {}, "face": {}}))
        graph = build_graph(c, {"identity_1": "body.png", "identity_2": "face.png"}, "test", self.info)
        self.assertEqual(graph["identity_1"]["inputs"]["image"], "body.png")
        self.assertEqual(graph["identity_2"]["inputs"]["image"], "face.png")
        self.assertNotIn("first_frame", graph["condition"]["inputs"])
        clean = self.config(mode="text", first="stale", last="stale", identity_images=["stale"])
        self.assertEqual(input_roles(clean), [])
        self.assertEqual(clean["identity_images"], [])

    def test_picture_numbers_and_saved_prompt_migration(self):
        for mode, start in [("first_reference", 2), ("both_reference", 2), ("last_reference", 1), ("reference", 1)]:
            with self.subTest(mode=mode):
                c = self.config(mode=mode, first="ref", last="ref", identity_images=["face", "ref"])
                graph = build_graph(c, {"first":"pose.png", "last":"end.png", "identity_1":"face.png", "identity_2":"body.png"}, "test", self.info)
                subject = json.loads(graph["condition"]["inputs"]["prompt"])["subject_definitions"]
                self.assertIn(f"<Picture {start}>, <Picture {start + 1}>", subject)
        old = "Start <Picture 3>; face <Picture 1>, body <Picture 2>, end <Picture 4>. <Subject 1> turns."
        expected = "Start <Picture 1>; face <Picture 2>, body <Picture 3>, end <Picture 4>. <Subject 1> turns."
        c = self.config(mode="both_reference", first="ref", last="ref", identity_images=["face", "ref"],
                        settings_version=2, prompt=old, sound=old, music=old, random_seed=False)
        for field in ("prompt", "sound", "music"):
            self.assertEqual(c[field], expected)
        self.assertEqual(c["settings_version"], 3)
        self.assertEqual(c, validate(json.loads(json.dumps(c)), self.info, {"ref":{}, "face":{}}))
        unchanged = self.config(mode="reference", identity_images=["face"], settings_version=2, prompt=old)
        self.assertEqual(unchanged["prompt"], old)

    def test_outdated_node_cannot_silently_use_wrong_picture_order(self):
        self.info["MiniMaxH3ImageAndReferenceToVideo"] = {}
        with self.assertRaisesRegex(ValueError, "numbering update"):
            self.config()

    def test_safetensors_hybrid_uses_native_loader(self):
        graph = build_graph(self.config(model=B20), {}, "test", self.info)
        self.assertEqual(graph["model"]["class_type"], "UNETLoader")
        self.assertEqual(graph["model"]["inputs"], {"unet_name": B20, "weight_dtype": "default"})

    def test_legacy_settings_infer_fl2va_without_changing_model_or_mode(self):
        raw = {**DEFAULTS, "model": FL2VA, "mode": "first", "first": "ref"}
        for key in ("architecture", "settings_version", "identity_images"):
            raw.pop(key)
        c = validate(raw, self.info, {"ref": {}})
        self.assertEqual((c["architecture"], c["model"], c["mode"]), ("fl2va", FL2VA, "first"))
        graph = build_graph(c, {"first": "a.png"}, "test", self.info)
        self.assertEqual(graph["condition"]["class_type"], "MiniMaxH3ImageToVideo")
        self.assertNotIn("audio_vae", graph["condition"]["inputs"])

    def test_reject_mismatched_architecture_missing_or_duplicate_references(self):
        for bad in [{"architecture": "fl2va"}, {"model": FL2VA},
                    {"mode": "first_reference", "first": "ref"},
                    {"mode": "reference", "identity_images": ["ref", "ref"]},
                    {"mode": "reference", "identity_images": ["missing"]},
                    {"identity_images": "ref"}, {"ref_image_size": "wrong"}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.config(**bad)
        missing = dict(self.info)
        missing.pop("MiniMaxH3ImageAndReferenceToVideo")
        with self.assertRaisesRegex(ValueError, "restart ComfyUI"):
            validate({**DEFAULTS, "mode": "text"}, missing, {})

    def test_ref2va_cannot_claim_a_timeline_anchor(self):
        base = {"architecture": "ref2va", "model": "MiniMax-H3-ref2va-Q4_0.gguf", "identity_images": ["ref"]}
        c = self.config(**base, mode="reference")
        graph = build_graph(c, {"identity_1": "art.png"}, "test", self.info)
        self.assertEqual(graph["condition"]["class_type"], "MiniMaxH3ReferenceToVideo")
        with self.assertRaisesRegex(ValueError, "not supported"):
            self.config(**base, mode="first_reference", first="ref")

    def test_character_reference_keeps_geometry_independent_of_frame_fit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "portrait.png"
            Image.new("RGBA", (80, 200), (20, 40, 80, 255)).save(path)
            c = self.config(width=256, height=128, fit="stretch")
            identity = Image.open(io.BytesIO(prepare_image(path, c, fit_to_canvas=False)))
            anchor = Image.open(io.BytesIO(prepare_image(path, c)))
            self.assertEqual(identity.size, (80, 200))
            self.assertEqual(anchor.size, (256, 128))


if __name__ == '__main__':
    unittest.main()
