import importlib.util
import os
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "extension" / "Scripts" / "python" / "writer_autocomplete.py"
OPTIONS_HANDLER = Path(__file__).resolve().parents[1] / "extension" / "options_handler.py"


def load_module():
    spec = importlib.util.spec_from_file_location("writer_autocomplete", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_options_handler():
    spec = importlib.util.spec_from_file_location("options_handler", OPTIONS_HANDLER)
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
        self.assertEqual(settings["allow_reasoning"], "false")
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
        self.assertIn("Use 7 words", joined)
        self.assertIn("Do not reason", joined)

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

    def test_partial_ghost_acceptance_updates_remaining_range(self):
        module = load_module()

        class Text:
            def __init__(self, value):
                self.value = value

            def createTextCursorByRange(self, text_range):
                return Range(self, text_range.start, text_range.end)

        class Range:
            def __init__(self, text, start, end):
                self.text = text
                self.start = start
                self.end = end
                self.CharColor = 0x999999
                self.CharPosture = "ITALIC"

            def getString(self):
                return self.text.value[self.start : self.end]

            def setString(self, value):
                self.text.value = self.text.value[: self.start] + value + self.text.value[self.end :]
                self.end = self.start + len(value)

            def getStart(self):
                return Range(self.text, self.start, self.start)

            def getEnd(self):
                return Range(self.text, self.end, self.end)

            def getText(self):
                return self.text

            def goRight(self, count, expand):
                if self.end + count > len(self.text.value):
                    return False
                if expand:
                    self.end += count
                else:
                    self.start = self.end + count
                    self.end = self.start
                return True

        class Doc:
            def __init__(self):
                self.view_cursor = Range(text, 0, 0)

        text = Text("ghost words")
        doc = Doc()
        original_view_cursor = module._view_cursor
        original_move = module._move_view_cursor_to_range
        module._view_cursor = lambda current_doc: current_doc.view_cursor
        module._move_view_cursor_to_range = lambda current_doc, text_range: None
        try:
            ghost = module.GhostCompletion(
                doc,
                Range(text, 0, len(text.value)),
                "ghost words",
                {"CharColor": 0x123456, "CharPosture": "NONE"},
            )
            self.assertFalse(ghost.accept_prefix(module._next_ghost_word_count(ghost.completion)))
        finally:
            module._view_cursor = original_view_cursor
            module._move_view_cursor_to_range = original_move

        self.assertEqual(text.value, "ghost words")
        self.assertEqual(ghost.completion, " words")
        self.assertEqual(ghost.text_range.getString(), " words")

    def test_partial_ghost_word_count_includes_leading_space(self):
        module = load_module()
        self.assertEqual(module._next_ghost_char_count(" hello"), 1)
        self.assertEqual(module._next_ghost_word_count(" hello world"), 6)
        self.assertEqual(module._next_ghost_word_count(" long-form writing"), 10)
        self.assertEqual(module._next_ghost_word_count(", and then"), 1)

    def test_key_handler_partially_accepts_ghost_with_right_arrow(self):
        module = load_module()

        class Event:
            def __init__(self, key_code, modifiers=0):
                self.KeyCode = key_code
                self.Modifiers = modifiers
                self.KeyChar = ""

        calls = []
        original_has_ghost = module._has_ghost
        original_accept_partial = module._accept_partial_ghost
        module._has_ghost = lambda doc: True
        module._accept_partial_ghost = lambda doc, unit: calls.append(unit) or True
        try:
            handler = module.LibreCompleteAIKeyHandler(object())
            self.assertTrue(handler.keyPressed(Event(module.RIGHT)))
            self.assertTrue(handler.keyPressed(Event(module.RIGHT, module.MOD1)))
        finally:
            module._has_ghost = original_has_ghost
            module._accept_partial_ghost = original_accept_partial

        self.assertEqual(calls, ["char", "word"])

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
        self.assertTrue(module._is_continuous_typing_event(Event("a")))
        self.assertTrue(module._is_continuous_typing_event(Event(" ")))

    def test_continuous_idle_timer_debounces_typing(self):
        module = load_module()

        class Event:
            KeyCode = 0
            Modifiers = 0

            def __init__(self, char):
                self.KeyChar = char

        class Doc:
            def getRuntimeUID(self):
                return "doc-with-idle-typing"

        class FakeTimer:
            instances = []

            def __init__(self, delay, target, args=()):
                self.delay = delay
                self.target = target
                self.args = args
                self.started = False
                self.cancelled = False
                self.daemon = False
                FakeTimer.instances.append(self)

            def start(self):
                self.started = True

            def cancel(self):
                self.cancelled = True

            def fire(self):
                self.target(*self.args)

        calls = []
        original_timer = module.threading.Timer
        original_load = module.load_settings
        original_start = module._start_continuous_request
        module.threading.Timer = FakeTimer
        module.load_settings = lambda: module.normalize_settings({"continuous_suggestions": "true"})
        module._start_continuous_request = lambda doc, settings, force=False: calls.append((doc, force)) or True
        try:
            doc = Doc()
            self.assertTrue(module._schedule_continuous_request_after_idle(doc, Event("t")))
            self.assertTrue(module._schedule_continuous_request_after_idle(doc, Event("o")))
            self.assertEqual(len(FakeTimer.instances), 2)
            self.assertTrue(FakeTimer.instances[0].cancelled)

            FakeTimer.instances[0].fire()
            self.assertEqual(calls, [])
            FakeTimer.instances[1].fire()
            self.assertEqual(calls, [(doc, False)])
        finally:
            module.threading.Timer = original_timer
            module.load_settings = original_load
            module._start_continuous_request = original_start
            module._clear_continuous_state("doc-with-idle-typing")

    def test_reconcile_continuous_completion_keeps_remaining_match(self):
        module = load_module()
        state, remaining = module._reconcile_continuous_completion(
            "This plugin",
            "This plugin is designed",
            " is designed to help you write faster",
        )
        self.assertEqual(state, "match")
        self.assertEqual(remaining, " to help you write faster")

    def test_reconcile_continuous_completion_rejects_different_typing(self):
        module = load_module()
        state, remaining = module._reconcile_continuous_completion(
            "This plugin",
            "This plugin can already",
            " is designed to help you write faster",
        )
        self.assertEqual(state, "mismatch")
        self.assertEqual(remaining, "")

    def test_continuous_word_threshold_uses_words_since_last_request(self):
        module = load_module()
        key = "doc-test"
        original = dict(module._LAST_AUTO_PREFIX)
        try:
            module._LAST_AUTO_PREFIX[key] = "The first sentence"
            self.assertFalse(module._has_enough_new_words_for_continuous(key, "The first sentence adds two"))
            self.assertTrue(module._has_enough_new_words_for_continuous(key, "The first sentence adds three words"))
        finally:
            module._LAST_AUTO_PREFIX.clear()
            module._LAST_AUTO_PREFIX.update(original)

    def test_enable_disable_helpers_are_silent_and_track_state(self):
        module = load_module()

        class Controller:
            def __init__(self):
                self.handlers = []

            def addKeyHandler(self, handler):
                self.handlers.append(handler)

            def removeKeyHandler(self, handler):
                self.handlers.remove(handler)

        class Doc:
            def __init__(self):
                self.controller = Controller()

            def supportsService(self, service_name):
                return service_name == "com.sun.star.text.TextDocument"

            def getCurrentController(self):
                return self.controller

        messages = []
        original_message_box = module._message_box
        module._message_box = lambda *args: messages.append(args)
        try:
            doc = Doc()
            self.assertFalse(module.is_autocomplete_enabled(doc))
            self.assertTrue(module.enable_autocomplete_for_doc(doc))
            self.assertTrue(module.is_autocomplete_enabled(doc))
            self.assertTrue(module.enable_autocomplete_for_doc(doc))
            self.assertEqual(len(doc.controller.handlers), 1)
            self.assertTrue(module.disable_autocomplete_for_doc(doc))
            self.assertFalse(module.is_autocomplete_enabled(doc))
            self.assertEqual(messages, [])
        finally:
            module._message_box = original_message_box

    def test_document_key_prefers_runtime_uid_over_proxy_identity(self):
        module = load_module()

        class Controller:
            def __init__(self):
                self.handlers = []

            def addKeyHandler(self, handler):
                self.handlers.append(handler)

            def removeKeyHandler(self, handler):
                self.handlers.remove(handler)

        class Doc:
            def __init__(self, controller):
                self.controller = controller

            def getRuntimeUID(self):
                return "same-libreoffice-document"

            def supportsService(self, service_name):
                return service_name == "com.sun.star.text.TextDocument"

            def getCurrentController(self):
                return self.controller

        controller = Controller()
        first_proxy = Doc(controller)
        second_proxy = Doc(controller)
        self.assertNotEqual(id(first_proxy), id(second_proxy))

        self.assertTrue(module.enable_autocomplete_for_doc(first_proxy))
        self.assertTrue(module.is_autocomplete_enabled(second_proxy))
        self.assertTrue(module.disable_autocomplete_for_doc(second_proxy))
        self.assertFalse(module.is_autocomplete_enabled(first_proxy))

    def test_toggle_continuous_suggestions_for_doc_updates_settings(self):
        module = load_module()
        stored = module.normalize_settings({"continuous_suggestions": "false"})

        original_load = module.load_settings
        original_save = module.save_settings
        module.load_settings = lambda: dict(stored)

        def fake_save(settings):
            stored.clear()
            stored.update(module.normalize_settings(settings))
            return dict(stored)

        module.save_settings = fake_save
        try:
            self.assertTrue(module.toggle_continuous_suggestions_for_doc())
            self.assertEqual(stored["continuous_suggestions"], "true")
            self.assertFalse(module.toggle_continuous_suggestions_for_doc())
            self.assertEqual(stored["continuous_suggestions"], "false")
        finally:
            module.load_settings = original_load
            module.save_settings = original_save

    def test_toolbar_protocol_url_command_parsing(self):
        module = load_options_handler()

        class Url:
            Complete = "vnd.librecompleteai:continuous"
            Path = ""
            Name = ""

        self.assertEqual(module._url_command(Url()), "continuous")

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
        self.assertEqual(payload["reasoning_effort"], "minimal")
        self.assertNotIn("max_tokens", payload)

    def test_request_openai_skips_reasoning_effort_when_allowed(self):
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
                    "allow_reasoning": "true",
                }
            )
            self.assertEqual(module._request_openai("before", "after", settings), "the next line")
        finally:
            module._post_json = original

        self.assertNotIn("reasoning_effort", calls[0][1])

    def test_request_openai_falls_back_when_reasoning_effort_is_unsupported(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append(dict(payload))
            if len(calls) == 1:
                raise module._HttpJsonError(
                    400,
                    url,
                    '{"error":{"code":"unsupported_parameter","param":"reasoning_effort"}}',
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

        self.assertEqual(calls[0]["reasoning_effort"], "minimal")
        self.assertNotIn("reasoning_effort", calls[1])

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
        self.assertEqual(calls[1]["reasoning_effort"], "minimal")

    def test_request_ollama_disables_thinking_by_default(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append((url, payload, headers))
            return {"response": "the next line", "thinking": "hidden scratchpad"}

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings({"provider": "ollama", "ollama_model": "qwen3"})
            self.assertEqual(module._request_ollama("before", "after", settings), "the next line")
        finally:
            module._post_json = original

        self.assertTrue(calls[0][0].endswith("/api/generate"))
        self.assertIs(calls[0][1]["raw"], True)
        self.assertIs(calls[0][1]["think"], False)
        self.assertEqual(calls[0][1]["prompt"], "before")
        self.assertIn("Okay, let me", calls[0][1]["options"]["stop"])

    def test_request_ollama_chat_adds_qwen_no_think_when_reasoning_is_disabled(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append((url, payload, headers))
            return {"message": {"content": "the next line"}}

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings({"provider": "ollama", "ollama_model": "qwen3:4b"})
            messages = module.build_summary_messages("Earlier document text.", settings)
            self.assertEqual(module._request_ollama_messages(messages, settings), "the next line")
        finally:
            module._post_json = original

        self.assertTrue(calls[0][0].endswith("/api/chat"))
        user_messages = [message for message in calls[0][1]["messages"] if message["role"] == "user"]
        self.assertTrue(user_messages)
        self.assertTrue(user_messages[0]["content"].rstrip().endswith("/no_think"))

    def test_request_ollama_allows_thinking_when_enabled(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append((url, payload, headers))
            return {"response": "the next line"}

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings(
                {"provider": "ollama", "ollama_model": "qwen3", "allow_reasoning": "true"}
            )
            self.assertEqual(module._request_ollama("before", "after", settings), "the next line")
        finally:
            module._post_json = original

        self.assertIs(calls[0][1]["think"], True)
        self.assertTrue(calls[0][0].endswith("/api/generate"))
        self.assertNotIn("Okay, let me", calls[0][1]["options"]["stop"])

    def test_clean_completion_strips_labels_and_fences(self):
        module = load_module()
        text = "```text\nCompletion: the candle bent toward the draft.\n```"
        self.assertEqual(module.clean_completion(text), "the candle bent toward the draft.")

    def test_clean_completion_strips_visible_reasoning_blocks(self):
        module = load_module()
        text = "<think>I should continue with a sentence.</think>\nCompletion: the candle bent toward the draft."
        self.assertEqual(module.clean_completion(text), "the candle bent toward the draft.")

    def test_clean_completion_extracts_labeled_answer_after_meta_reasoning(self):
        module = load_module()
        text = (
            "Okay, let me tackle this. The user wants me to act as an inline autocomplete engine.\n\n"
            "First, I need to inspect the text before the cursor and think about word count.\n\n"
            'Possible continuation: "how it transforms your writing process without disrupting your creative flow." '
            "Let me count the words."
        )
        self.assertEqual(
            module.clean_completion(text, max_words=24),
            "how it transforms your writing process without disrupting your creative flow.",
        )

    def test_clean_completion_discards_unlabeled_meta_reasoning(self):
        module = load_module()
        text = "Okay, let me tackle this. The user wants me to continue text at the cursor."
        self.assertEqual(module.clean_completion(text), "")

    def test_clean_completion_caps_overlong_completion(self):
        module = load_module()
        text = "one two three four five six"
        self.assertEqual(module.clean_completion(text, max_words=4), "one two three four")

    def test_clean_completion_removes_echoed_prefix_tail(self):
        module = load_module()
        prefix = "She opened the notebook and wrote"
        completion = "notebook and wrote one sentence before stopping."
        self.assertEqual(module.clean_completion(completion, prefix), " one sentence before stopping.")

    def test_completion_spacing_adds_missing_space_after_word(self):
        module = load_module()
        self.assertEqual(
            module._completion_with_context_spacing("We could run into", "problems"),
            " problems",
        )
        self.assertEqual(
            module._completion_with_context_spacing("We could run into", " problems"),
            " problems",
        )
        self.assertEqual(
            module._completion_with_context_spacing("We could run into", "."),
            ".",
        )
        self.assertEqual(
            module._completion_with_context_spacing("We could run into ", "problems"),
            "problems",
        )

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
