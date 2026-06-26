import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "extension" / "Scripts" / "python" / "writer_autocomplete.py"


def load_module():
    spec = importlib.util.spec_from_file_location("writer_autocomplete", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WriterAutocompleteTests(unittest.TestCase):
    def test_normalize_settings_defaults_to_supported_provider(self):
        module = load_module()
        settings = module.normalize_settings({"provider": "bad"})
        self.assertEqual(settings["provider"], "openai")
        self.assertEqual(settings["openai_base_url"], "https://api.openai.com/v1")

    def test_build_messages_is_insert_only(self):
        module = load_module()
        settings = module.normalize_settings({"provider": "ollama", "max_tokens": "40"})
        messages = module.build_messages("The room was quiet", "afterward.", settings)
        joined = "\n".join(message["content"] for message in messages)
        self.assertIn("Return only text", joined)
        self.assertIn("The room was quiet", joined)
        self.assertIn("afterward.", joined)

    def test_clean_completion_strips_labels_and_fences(self):
        module = load_module()
        text = "```text\nCompletion: the candle bent toward the draft.\n```"
        self.assertEqual(module.clean_completion(text), "the candle bent toward the draft.")

    def test_clean_completion_removes_echoed_prefix_tail(self):
        module = load_module()
        prefix = "She opened the notebook and wrote"
        completion = "notebook and wrote one sentence before stopping."
        self.assertEqual(module.clean_completion(completion, prefix), " one sentence before stopping.")

    def test_capture_and_apply_properties(self):
        module = load_module()

        class Range:
            CharColor = 0x123456
            CharPosture = "NONE"

        source = Range()
        target = Range()
        values = module._capture_properties(source, ("CharColor", "CharPosture", "Missing"))
        self.assertEqual(values, {"CharColor": 0x123456, "CharPosture": "NONE"})

        module._apply_properties(target, {"CharColor": 0xABCDEF, "CharPosture": "ITALIC"})
        self.assertEqual(target.CharColor, 0xABCDEF)
        self.assertEqual(target.CharPosture, "ITALIC")

    def test_go_left_chunks_long_distances(self):
        module = load_module()

        class Cursor:
            def __init__(self):
                self.calls = []

            def goLeft(self, count, expand):
                self.calls.append((count, expand))
                return True

        cursor = Cursor()
        self.assertTrue(module._go_left(cursor, 65001, True))
        self.assertEqual(cursor.calls, [(32000, True), (32000, True), (1001, True)])


if __name__ == "__main__":
    unittest.main()
