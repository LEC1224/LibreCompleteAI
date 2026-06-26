import importlib.util
import os
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
        self.assertEqual(settings["continuous_suggestions"], "false")
        self.assertEqual(settings["max_context_words"], "600")
        self.assertEqual(settings["prediction_words"], "24")

    def test_normalize_settings_does_not_copy_env_api_key(self):
        module = load_module()
        original = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-test"
        try:
            settings = module.normalize_settings({})
            self.assertEqual(settings["openai_api_key"], "")
        finally:
            if original is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = original

    def test_build_messages_is_insert_only(self):
        module = load_module()
        settings = module.normalize_settings({"provider": "ollama", "max_tokens": "40", "prediction_words": "7"})
        messages = module.build_messages("The room was quiet", "afterward.", settings)
        joined = "\n".join(message["content"] for message in messages)
        self.assertIn("Return only text", joined)
        self.assertIn("The room was quiet", joined)
        self.assertIn("afterward.", joined)
        self.assertIn("Aim for 7 words", joined)

    def test_context_compression_summarizes_older_context(self):
        module = load_module()
        original = module._summarize_context
        calls = []

        def fake_summarize(text, settings, cache_key=None):
            calls.append((text, cache_key))
            return "Earlier summary."

        module._summarize_context = fake_summarize
        try:
            prefix = " ".join(f"word{i}" for i in range(150))
            settings = module.normalize_settings({"max_context_words": "40"})
            compressed = module._compress_context_if_needed(prefix, settings, cache_key="doc1")
        finally:
            module._summarize_context = original

        self.assertIn("[Compressed earlier context]", compressed)
        self.assertIn("Earlier summary.", compressed)
        self.assertIn("word149", compressed)
        self.assertTrue(calls)

    def test_discard_ghost_restores_insertion_properties(self):
        module = load_module()

        class Range:
            def __init__(self, text="ghost"):
                self.text = text
                self.CharColor = 0x999999
                self.CharPosture = "ITALIC"

            def getString(self):
                return self.text

            def setString(self, value):
                self.text = value

            def getStart(self):
                return self

            def getEnd(self):
                return self

        class Doc:
            def __init__(self):
                self.view_cursor = Range("")

        doc = Doc()
        original_view_cursor = module._view_cursor
        original_move = module._move_view_cursor_to_range
        module._view_cursor = lambda current_doc: current_doc.view_cursor
        module._move_view_cursor_to_range = lambda current_doc, text_range: None
        try:
            ghost_range = Range("ghost")
            ghost = module.GhostCompletion(
                doc,
                ghost_range,
                "ghost",
                {"CharColor": 0x123456, "CharPosture": "NONE"},
            )
            ghost.discard()
        finally:
            module._view_cursor = original_view_cursor
            module._move_view_cursor_to_range = original_move

        self.assertEqual(ghost_range.getString(), "")
        self.assertEqual(doc.view_cursor.CharColor, 0x123456)
        self.assertEqual(doc.view_cursor.CharPosture, "NONE")

    def test_continuous_trigger_uses_writing_boundaries(self):
        module = load_module()

        class Event:
            KeyCode = 0
            Modifiers = 0

            def __init__(self, char):
                self.KeyChar = char

        self.assertTrue(module._is_continuous_trigger(Event(" ")))
        self.assertTrue(module._is_continuous_trigger(Event(".")))
        self.assertFalse(module._is_continuous_trigger(Event("a")))

    def test_request_openai_uses_max_completion_tokens(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append((url, payload, headers))
            return {"choices": [{"message": {"content": "the next line"}}]}

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings(
                {
                    "openai_api_key": "sk-test",
                    "openai_model": "gpt-5.4-mini",
                    "max_tokens": "32",
                }
            )
            self.assertEqual(module._request_openai("before", "after", settings), "the next line")
        finally:
            module._post_json = original

        payload = calls[0][1]
        self.assertEqual(payload["max_completion_tokens"], 32)
        self.assertNotIn("max_tokens", payload)

    def test_request_openai_falls_back_to_max_tokens_for_compatible_servers(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append(dict(payload))
            if len(calls) == 1:
                raise module._HttpJsonError(
                    400,
                    url,
                    '{"error":{"code":"unsupported_parameter","param":"max_completion_tokens"}}',
                )
            return {"choices": [{"message": {"content": "fallback line"}}]}

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings(
                {
                    "openai_api_key": "sk-test",
                    "openai_model": "compatible-model",
                    "max_tokens": "24",
                }
            )
            self.assertEqual(module._request_openai("before", "after", settings), "fallback line")
        finally:
            module._post_json = original

        self.assertIn("max_completion_tokens", calls[0])
        self.assertNotIn("max_tokens", calls[0])
        self.assertEqual(calls[1]["max_tokens"], 24)
        self.assertNotIn("max_completion_tokens", calls[1])

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
