import importlib.util
import os
import tempfile
import threading
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
        settings = module.normalize_settings({"provider": "bad", "suffix_chars": "999"})
        self.assertEqual(settings["provider"], "openai")
        self.assertNotIn("suffix_chars", settings)
        self.assertEqual(settings["openai_base_url"], "https://api.openai.com/v1")
        self.assertEqual(settings["autocomplete_enabled"], "false")
        self.assertEqual(settings["continuous_suggestions"], "false")
        self.assertEqual(settings["allow_reasoning"], "false")
        self.assertEqual(settings["ollama_model"], "qwen3.5:4b")
        self.assertEqual(settings["ollama_completion_mode"], "auto")
        self.assertEqual(settings["codex_executable"], "codex")
        self.assertEqual(settings["codex_model"], "")
        self.assertEqual(settings["max_context_words"], "600")
        self.assertEqual(settings["prediction_words"], "24")
        self.assertEqual(settings["reference_files"], "")

    def test_normalize_settings_accepts_persisted_autocomplete_state(self):
        module = load_module()
        self.assertEqual(
            module.normalize_settings({"autocomplete_enabled": True})["autocomplete_enabled"],
            "true",
        )
        self.assertEqual(
            module.normalize_settings({"autocomplete_enabled": "off"})["autocomplete_enabled"],
            "false",
        )

    def test_autocomplete_preference_roundtrips_through_settings(self):
        module = load_module()
        stored = module.normalize_settings({"autocomplete_enabled": "false"})
        original_load = module.load_settings
        original_save = module.save_settings
        module.load_settings = lambda: dict(stored)

        def fake_save(settings):
            stored.clear()
            stored.update(module.normalize_settings(settings))
            return dict(stored)

        module.save_settings = fake_save
        try:
            self.assertTrue(module.set_autocomplete_preference(True))
            self.assertTrue(module.is_autocomplete_preferred())
            self.assertFalse(module.set_autocomplete_preference(False))
            self.assertFalse(module.is_autocomplete_preferred())
        finally:
            module.load_settings = original_load
            module.save_settings = original_save

    def test_restore_autocomplete_enables_only_when_preferred(self):
        module = load_module()
        doc = object()
        enabled = []
        original_load = module.load_settings
        original_enable = module.enable_autocomplete_for_doc
        module.enable_autocomplete_for_doc = lambda current_doc: enabled.append(current_doc) or True
        try:
            module.load_settings = lambda: module.normalize_settings(
                {"autocomplete_enabled": "true"}
            )
            self.assertTrue(module.restore_autocomplete_for_doc(doc))
            self.assertEqual(enabled, [doc])

            module.load_settings = lambda: module.normalize_settings(
                {"autocomplete_enabled": "false"}
            )
            self.assertFalse(module.restore_autocomplete_for_doc(doc))
            self.assertEqual(enabled, [doc])
        finally:
            module.load_settings = original_load
            module.enable_autocomplete_for_doc = original_enable

    def test_toggle_autocomplete_remembers_each_resulting_mode(self):
        module = load_module()

        class Doc:
            pass

        doc = Doc()
        key = module._doc_key(doc)
        preferences = []
        original_enable = module.enable_autocomplete_for_doc
        original_disable = module.disable_autocomplete_for_doc
        original_preference = module.set_autocomplete_preference

        def fake_enable(current_doc, notify=False):
            module._HANDLERS[key] = (object(), object())
            return True

        def fake_disable(current_doc, notify=False):
            module._HANDLERS.pop(key, None)
            return True

        module.enable_autocomplete_for_doc = fake_enable
        module.disable_autocomplete_for_doc = fake_disable
        module.set_autocomplete_preference = lambda enabled: preferences.append(enabled)
        module._HANDLERS.pop(key, None)
        try:
            self.assertTrue(module.toggle_autocomplete_for_doc(doc))
            self.assertFalse(module.toggle_autocomplete_for_doc(doc))
        finally:
            module._HANDLERS.pop(key, None)
            module.enable_autocomplete_for_doc = original_enable
            module.disable_autocomplete_for_doc = original_disable
            module.set_autocomplete_preference = original_preference

        self.assertEqual(preferences, [True, False])

    def test_normalize_settings_keeps_unique_markdown_and_text_reference_files(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canon = root / "canon.md"
            characters = root / "characters.TXT"
            ignored = root / "notes.pdf"
            settings = module.normalize_settings(
                {
                    "reference_files": [
                        str(canon),
                        str(characters),
                        str(canon),
                        str(ignored),
                    ]
                }
            )

        self.assertEqual(
            settings["reference_files"].splitlines(),
            [str(canon.resolve()), str(characters.resolve())],
        )

    def test_normalize_settings_accepts_codex_provider(self):
        module = load_module()
        settings = module.normalize_settings(
            {
                "provider": "Codex",
                "codex_executable": "C:/Tools/codex.exe",
                "codex_model": "gpt-5.4",
            }
        )
        self.assertEqual(settings["provider"], "codex")
        self.assertEqual(settings["codex_executable"], "C:/Tools/codex.exe")
        self.assertEqual(settings["codex_model"], "gpt-5.4")

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

    def test_normalize_settings_accepts_libreoffice_decimal_commas(self):
        module = load_module()
        settings = module.normalize_settings(
            {
                "prediction_words": "50,00",
                "max_context_words": "1.200,00",
                "temperature": "0,45",
            }
        )
        self.assertEqual(settings["prediction_words"], "50")
        self.assertEqual(settings["max_context_words"], "1200")
        self.assertEqual(settings["temperature"], "0.45")
        self.assertEqual(module._prediction_words(settings), 50)

    def test_normalize_settings_rejects_unknown_ollama_completion_mode(self):
        module = load_module()
        self.assertEqual(
            module.normalize_settings({"ollama_completion_mode": "BAD"})["ollama_completion_mode"],
            "auto",
        )
        self.assertEqual(
            module.normalize_settings({"ollama_completion_mode": " Guided "})[
                "ollama_completion_mode"
            ],
            "guided",
        )

    def test_build_messages_is_insert_only(self):
        module = load_module()
        settings = module.normalize_settings({"provider": "ollama", "max_tokens": "40", "prediction_words": "7"})
        messages = module.build_messages("The room was quiet", "", settings)
        joined = "\n".join(message["content"] for message in messages)
        self.assertIn("Return only text", joined)
        self.assertIn("The room was quiet", joined)
        self.assertIn("Aim for 7 words", joined)
        self.assertIn("use 5-7 words", joined)
        self.assertIn("Never exceed 7 words", joined)
        self.assertIn("Do not reason", joined)

    def test_build_messages_ignores_after_cursor_text(self):
        module = load_module()
        settings = module.normalize_settings({"prediction_words": "100"})
        messages = module.build_messages(
            "The dragon's wings flattened the grass around him.",
            "Elian's heart hammered as the dragon landed.",
            settings,
        )
        joined = "\n".join(message["content"] for message in messages)
        self.assertIn("Treat <cursor> as the current end of the document", joined)
        self.assertIn("use 80-100 words", joined)
        self.assertNotIn("Elian's heart hammered as the dragon landed.", joined)
        self.assertNotIn("<after>", joined)

    def test_build_messages_includes_reference_file_contents(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canon = root / "canon.md"
            characters = root / "characters.txt"
            canon.write_text("The northern moon is called Ilyra.", encoding="utf-8")
            characters.write_text("Mara never removes her silver gloves.", encoding="utf-8")
            settings = module.normalize_settings(
                {"reference_files": [str(canon), str(characters)]}
            )
            messages = module.build_messages("Mara looked up.", "", settings)

        joined = "\n".join(message["content"] for message in messages)
        self.assertIn("Reference material:", joined)
        self.assertIn("Reference file: canon.md", joined)
        self.assertIn("The northern moon is called Ilyra.", joined)
        self.assertIn("Reference file: characters.txt", joined)
        self.assertIn("Mara never removes her silver gloves.", joined)
        self.assertNotIn(str(root), joined)

    def test_codex_messages_leave_references_for_app_server_session_cache(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "canon.md"
            reference.write_text("The northern moon is called Ilyra.", encoding="utf-8")
            settings = module.normalize_settings(
                {"provider": "codex", "reference_files": str(reference)}
            )
            messages = module.build_messages("Mara looked up.", "", settings)
            reference_context, reference_hash = module._reference_context_snapshot(settings)

        joined = "\n".join(message["content"] for message in messages)
        self.assertNotIn("The northern moon is called Ilyra.", joined)
        self.assertIn("The northern moon is called Ilyra.", reference_context)
        self.assertEqual(len(reference_hash), 64)

    def test_codex_app_server_uses_lowest_effort_supported_by_each_model(self):
        module = load_module()
        client = object.__new__(module._CodexAppServerClient)
        client.model_reasoning_efforts = {}
        client._rpc = lambda *args, **kwargs: {
            "data": [
                {
                    "id": "gpt-minimal",
                    "model": "gpt-minimal",
                    "isDefault": True,
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "minimal"},
                        {"reasoningEffort": "high"},
                    ],
                },
                {
                    "id": "gpt-low-only",
                    "model": "gpt-low-only",
                    "isDefault": False,
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low"},
                        {"reasoningEffort": "medium"},
                    ],
                },
            ]
        }

        client._load_model_reasoning_efforts()

        self.assertEqual(client._reasoning_effort_for_model("gpt-minimal"), "minimal")
        self.assertEqual(client._reasoning_effort_for_model("gpt-low-only"), "low")
        self.assertEqual(client._reasoning_effort_for_model(""), "minimal")
        self.assertEqual(client._reasoning_effort_for_model("unknown-model"), "low")

    def test_codex_session_prewarm_prepares_reference_base(self):
        module = load_module()
        prepared = []

        class Client:
            def prepare(self, settings):
                prepared.append(settings)

        original_resolve = module._resolve_codex_executable
        original_client = module._codex_app_server_client
        module._resolve_codex_executable = lambda configured: "C:/Tools/codex.exe"
        module._codex_app_server_client = lambda executable: Client()
        try:
            settings = module.normalize_settings({"provider": "codex"})
            module._prewarm_codex_session(settings)
        finally:
            module._resolve_codex_executable = original_resolve
            module._codex_app_server_client = original_client

        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0]["provider"], "codex")

    def test_codex_reference_base_thread_rebuilds_only_when_content_hash_changes(self):
        module = load_module()

        class WorkingDirectory:
            name = "C:/temporary-codex-session"

        client = object.__new__(module._CodexAppServerClient)
        client.reference_base_key = None
        client.reference_base_thread_id = None
        client.working_directory = WorkingDirectory()
        calls = []
        base_count = [0]

        def fake_rpc(method, params, timeout, unavailable_on_error=False):
            calls.append((method, params))
            if method == "thread/start":
                base_count[0] += 1
                return {"thread": {"id": f"base-{base_count[0]}"}}
            return {}

        client._rpc = fake_rpc
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "canon.md"
            reference.write_text("The moon is called Ilyra.", encoding="utf-8")
            settings = module.normalize_settings(
                {"provider": "codex", "reference_files": str(reference)}
            )

            first = client._ensure_reference_base_thread(settings)
            calls_after_first = len(calls)
            second = client._ensure_reference_base_thread(settings)
            self.assertEqual(len(calls), calls_after_first)

            original_stat = reference.stat()
            reference.write_text("The moon is called Veyra.", encoding="utf-8")
            os.utime(
                reference,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            third = client._ensure_reference_base_thread(settings)

        self.assertEqual((first, second, third), ("base-1", "base-1", "base-2"))
        methods = [method for method, _params in calls]
        self.assertEqual(
            methods,
            [
                "thread/start",
                "thread/inject_items",
                "thread/start",
                "thread/inject_items",
                "thread/delete",
            ],
        )
        first_injection = calls[1][1]["items"][0]["content"][0]["text"]
        second_injection = calls[3][1]["items"][0]["content"][0]["text"]
        self.assertIn("Ilyra", first_injection)
        self.assertIn("Veyra", second_injection)

    def test_codex_app_server_interrupts_active_turn_when_request_is_cancelled(self):
        module = load_module()

        class Process:
            def poll(self):
                return None

        class WorkingDirectory:
            name = "C:/temporary-codex-session"

        client = object.__new__(module._CodexAppServerClient)
        client.lock = threading.RLock()
        client.process = Process()
        client.working_directory = WorkingDirectory()
        client._ensure_reference_base_thread = lambda settings: "base-thread"
        client._unsubscribe_thread = lambda thread_id: None
        calls = []

        def fake_rpc(method, params, timeout, unavailable_on_error=False):
            calls.append((method, params))
            if method == "thread/fork":
                return {"thread": {"id": "request-thread"}}
            if method == "turn/start":
                return {"turn": {"id": "request-turn"}}
            return {}

        cancel_event = threading.Event()

        def fake_next_message(deadline, poll_interval=None):
            cancel_event.set()
            return None

        client._rpc = fake_rpc
        client._next_message = fake_next_message
        module._REQUEST_CONTEXT.cancel_event = cancel_event
        try:
            with self.assertRaises(module._RequestCancelled):
                client.complete(
                    [{"role": "user", "content": "Continue."}],
                    module.normalize_settings({"provider": "codex"}),
                )
        finally:
            del module._REQUEST_CONTEXT.cancel_event

        self.assertIn(
            (
                "turn/interrupt",
                {"threadId": "request-thread", "turnId": "request-turn"},
            ),
            calls,
        )
        turn_start = next(params for method, params in calls if method == "turn/start")
        self.assertEqual(turn_start["effort"], "low")

    def test_reference_context_skips_missing_and_unsupported_files(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing.md"
            unsupported = root / "notes.pdf"
            unsupported.write_text("Do not include this.", encoding="utf-8")
            settings = dict(module.DEFAULT_SETTINGS)
            settings["reference_files"] = "\n".join((str(missing), str(unsupported)))
            block = module._reference_context_block(settings)

        self.assertEqual(block, "")

    def test_ollama_raw_prompt_includes_reference_context_when_configured(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "world.md"
            reference.write_text("Magic always has a visible cost.", encoding="utf-8")
            settings = module.normalize_settings(
                {
                    "writing_guidance": "Use close third person.",
                    "reference_files": str(reference),
                }
            )
            prompt = module._ollama_completion_prompt("Mara raised her hand.", "", settings)

        self.assertIn("Use close third person.", prompt)
        self.assertIn("Magic always has a visible cost.", prompt)
        self.assertTrue(prompt.endswith("Mara raised her hand."))

    def test_completion_token_limit_tracks_prediction_words_below_hard_cap(self):
        module = load_module()
        short = module.normalize_settings({"prediction_words": "7", "max_tokens": "100"})
        long = module.normalize_settings({"prediction_words": "50,00", "max_tokens": "100"})
        capped = module.normalize_settings({"prediction_words": "120", "max_tokens": "64"})

        self.assertEqual(module._completion_token_limit(short), 14)
        self.assertEqual(module._completion_token_limit(long), 100)
        self.assertEqual(module._completion_token_limit(capped), 64)

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

    def test_interactive_completion_uses_one_provider_call_for_long_context(self):
        module = load_module()
        captured = []
        originals = {
            "_generate_completion_once": module._generate_completion_once,
            "_summarize_context": module._summarize_context,
        }
        module._generate_completion_once = lambda prefix, suffix, settings, regeneration=None, on_retry=None: (
            captured.append(prefix) or "a continuation"
        )
        module._summarize_context = lambda *args, **kwargs: self.fail(
            "interactive completion should not make a summary request"
        )
        try:
            prefix = " ".join(f"word{i}" for i in range(150))
            settings = module.normalize_settings({"max_context_words": "40"})
            completion = module.request_completion(prefix, "", settings, cache_key="fast-doc")
        finally:
            for name, value in originals.items():
                setattr(module, name, value)

        self.assertEqual(completion, "a continuation")
        self.assertEqual(len(captured), 1)
        self.assertIn("[Opening excerpt]", captured[0])
        self.assertIn("[Recent text before cursor]", captured[0])
        self.assertIn("word149", captured[0])

    def test_text_context_counts_backward_from_cursor_not_document_end(self):
        module = load_module()

        class Text:
            def __init__(self, value):
                self.value = value

            def createTextCursorByRange(self, text_range):
                return Range(self, text_range.start, text_range.end)

        class Range:
            def __init__(self, text, start, end=None):
                self.text = text
                self.start = start
                self.end = start if end is None else end

            def isCollapsed(self):
                return self.start == self.end

            def getText(self):
                return self.text

            def getStart(self):
                return Range(self.text, self.start)

            def getEnd(self):
                return Range(self.text, self.end)

            def getString(self):
                return self.text.value[self.start : self.end]

            def goLeft(self, count, expand):
                destination = max(0, self.start - count)
                moved_all = self.start - destination == count
                if expand:
                    self.start = destination
                else:
                    self.start = destination
                    self.end = destination
                return moved_all

            def goRight(self, count, expand):
                destination = min(len(self.text.value), self.end + count)
                moved_all = destination - self.end == count
                if expand:
                    self.end = destination
                else:
                    self.start = destination
                    self.end = destination
                return moved_all

        class Doc:
            pass

        text = Text("0123456789abcdefghij")
        doc = Doc()
        doc.view_cursor = Range(text, 10)
        settings = module.normalize_settings({"max_context_words": "0", "prefix_chars": "4"})
        original_view_cursor = module._view_cursor
        module._view_cursor = lambda current_doc: current_doc.view_cursor
        try:
            _cursor, prefix, suffix = module._get_text_context(doc, settings)
        finally:
            module._view_cursor = original_view_cursor

        self.assertEqual(prefix, "6789")
        self.assertEqual(suffix, "")
        self.assertNotIn("abcdefghij", prefix)

    def test_escape_regeneration_changes_direction_without_shrinking_target(self):
        module = load_module()

        class Range:
            def __init__(self, value):
                self.value = value

            def getString(self):
                return self.value

            def setString(self, value):
                self.value = value

            def getStart(self):
                return self

        class Doc:
            def getRuntimeUID(self):
                return "retry-document"

        doc = Doc()
        key = module._doc_key(doc)
        original_move = module._move_view_cursor_to_range
        original_restore = module._restore_insertion_properties
        module._move_view_cursor_to_range = lambda *args: None
        module._restore_insertion_properties = lambda *args: None
        try:
            first = module.GhostCompletion(
                doc,
                Range(" first rejected direction"),
                " first rejected direction",
                {},
                request_prefix="Before cursor",
                regeneration_attempt=0,
            )
            module._GHOSTS[key] = first
            self.assertTrue(module._reject_ghost(doc))
            regeneration = module._regeneration_for_context(key, "Before cursor", "After cursor")

            second = module.GhostCompletion(
                doc,
                Range(" second rejected direction"),
                " second rejected direction",
                {},
                request_prefix="Before cursor",
                regeneration_attempt=regeneration["attempt"],
            )
            module._GHOSTS[key] = second
            self.assertTrue(module._reject_ghost(doc))
            regeneration = module._regeneration_for_context(key, "Before cursor", "After cursor")

            settings = module.normalize_settings({"prediction_words": "40", "max_tokens": "96"})
            prompt = module.build_messages(
                "Before cursor", "After cursor", settings, regeneration=regeneration
            )[1]["content"]
        finally:
            module._move_view_cursor_to_range = original_move
            module._restore_insertion_properties = original_restore
            module._GHOSTS.pop(key, None)
            module._REGENERATION_STATES.pop(key, None)

        self.assertEqual(regeneration["attempt"], 2)
        self.assertEqual(
            regeneration["rejected"],
            [" first rejected direction", " second rejected direction"],
        )
        self.assertIn("Regeneration attempt 2", prompt)
        self.assertIn("first rejected direction", prompt)
        self.assertIn("second rejected direction", prompt)
        self.assertIn("Keep the requested length near 40 words", prompt)
        self.assertIn("do not shorten it merely because this is a retry", prompt)
        self.assertEqual(module._completion_token_limit(settings), 80)

    def test_regeneration_state_resets_when_cursor_context_changes(self):
        module = load_module()
        key = "retry-context-change"
        module._REGENERATION_STATES[key] = {
            "prefix": "original prefix",
            "attempt": 2,
            "rejected": ["one", "two"],
        }
        try:
            self.assertIsNone(module._regeneration_for_context(key, "edited prefix", "original suffix"))
            self.assertNotIn(key, module._REGENERATION_STATES)
        finally:
            module._REGENERATION_STATES.pop(key, None)

    def test_empty_manual_completion_silently_removes_request_indicator(self):
        module = load_module()

        class Doc:
            def getRuntimeUID(self):
                return "quiet-seam-document"

        class Status:
            def start(self, *args):
                pass

            def end(self):
                pass

        doc = Doc()
        messages = []
        indicators = []

        class Indicator:
            def __init__(self):
                self.removed = False

            def remove(self):
                self.removed = True

        originals = {
            "_is_writer_document": module._is_writer_document,
            "_has_ghost": module._has_ghost,
            "load_settings": module.load_settings,
            "_status_indicator": module._status_indicator,
            "_get_text_context": module._get_text_context,
            "request_completion": module.request_completion,
            "_message_box": module._message_box,
            "_start_daemon_thread": module._start_daemon_thread,
            "_show_request_indicator": module._show_request_indicator,
        }
        module._is_writer_document = lambda current_doc: True
        module._has_ghost = lambda current_doc: False
        module.load_settings = lambda: module.normalize_settings({})
        module._status_indicator = lambda current_doc: Status()
        module._get_text_context = lambda current_doc, settings: (
            object(),
            "before",
            "existing after",
        )
        module.request_completion = lambda *args, **kwargs: ""
        module._message_box = lambda *args: messages.append(args)
        module._start_daemon_thread = lambda target, args: target(*args)
        module._show_request_indicator = lambda *args, **kwargs: indicators.append(Indicator()) or indicators[-1]
        try:
            module.complete_current_position(doc, preview=True, quiet=False)
        finally:
            for name, value in originals.items():
                setattr(module, name, value)
            module._BUSY_DOCS.discard("quiet-seam-document")

        self.assertEqual(messages, [])
        self.assertEqual(len(indicators), 1)
        self.assertTrue(indicators[0].removed)

    def test_manual_completion_schedules_provider_work_without_blocking_key_handler(self):
        module = load_module()

        class Doc:
            def getRuntimeUID(self):
                return "async-manual-document"

        class Status:
            def __init__(self):
                self.started = False
                self.ended = False

            def start(self, *args):
                self.started = True

            def end(self):
                self.ended = True

        doc = Doc()
        status = Status()
        scheduled = []
        indicators = []

        class Indicator:
            def __init__(self):
                self.removed = False

            def remove(self):
                self.removed = True

        originals = {
            "_is_writer_document": module._is_writer_document,
            "_has_ghost": module._has_ghost,
            "_has_selected_text": module._has_selected_text,
            "load_settings": module.load_settings,
            "_status_indicator": module._status_indicator,
            "_get_text_context": module._get_text_context,
            "_start_daemon_thread": module._start_daemon_thread,
            "_show_request_indicator": module._show_request_indicator,
        }
        module._is_writer_document = lambda current_doc: True
        module._has_ghost = lambda current_doc: False
        module._has_selected_text = lambda current_doc: False
        module.load_settings = lambda: module.normalize_settings({})
        module._status_indicator = lambda current_doc: status
        module._get_text_context = lambda current_doc, settings: (object(), "before", "")
        module._start_daemon_thread = lambda target, args: scheduled.append((target, args))
        module._show_request_indicator = lambda *args, **kwargs: indicators.append(Indicator()) or indicators[-1]
        try:
            module.complete_current_position(doc, preview=True)
            self.assertEqual(len(scheduled), 1)
            self.assertTrue(status.started)
            self.assertFalse(status.ended)
            self.assertIn("async-manual-document", module._BUSY_DOCS)
            self.assertEqual(len(indicators), 1)
            self.assertFalse(indicators[0].removed)
        finally:
            module._finish_manual_request("async-manual-document")
            for name, value in originals.items():
                setattr(module, name, value)

        self.assertTrue(status.ended)
        self.assertTrue(indicators[0].removed)

    def _patch(self, module, **replacements):
        originals = {name: getattr(module, name) for name in replacements}
        for name, value in replacements.items():
            setattr(module, name, value)

        def restore():
            for name, value in originals.items():
                setattr(module, name, value)

        self.addCleanup(restore)

    def _patch_retry_hint_ui(self, module):
        """Run UI marshalling and the hint's minimum-visibility delay inline.

        A real (but detached) pyuno module can be importable outside
        LibreOffice, in which case _post_to_ui queues onto an AsyncCallback
        that nothing ever pumps. Forcing it synchronous keeps these tests about
        the wiring itself; the delay has dedicated tests further down.
        Returns the list of status indicators the hint creates.
        """
        statuses = []

        class Status:
            def __init__(self):
                self.texts = []
                self.ended = False

            def start(self, text, value):
                self.texts.append(text)

            def end(self):
                self.ended = True

        def status_factory(current_doc):
            statuses.append(Status())
            return statuses[-1]

        self._patch(
            module,
            _post_to_ui=lambda callback: callback(),
            _schedule_delayed=lambda seconds, callback: callback(),
            _status_indicator=status_factory,
        )
        return statuses

    def _start_manual_request(self, module, doc_key, revise=False):
        """Start a manual request through complete_current_position.

        The worker is captured instead of started, so the test can run it
        (and act in the middle of it) synchronously. statuses[0] is the
        normal "Generating..."/"Revising..." indicator; the retry hint, if
        any, is the next one.
        """
        statuses = self._patch_retry_hint_ui(module)
        scheduled = []

        class Doc:
            def getRuntimeUID(self):
                return doc_key

        class Indicator:
            def remove(self):
                pass

        self._patch(
            module,
            _is_writer_document=lambda current_doc: True,
            _has_ghost=lambda current_doc: False,
            _has_selected_text=lambda current_doc: revise,
            load_settings=lambda: module.normalize_settings({}),
            _get_text_context=lambda current_doc, settings: (object(), "before", ""),
            _get_revision_context=lambda current_doc: (object(), "teh cat", "", ""),
            _start_daemon_thread=lambda target, args: scheduled.append((target, args)),
            _show_request_indicator=lambda *args, **kwargs: Indicator(),
        )
        self.addCleanup(module._BUSY_DOCS.discard, doc_key)
        doc = Doc()
        module.complete_current_position(doc, preview=True)
        self.assertEqual(len(scheduled), 1)
        target, args = scheduled[0]
        return doc, statuses, lambda: target(*args)

    def test_manual_completion_keeps_retry_hint_after_generating_status_ends(self):
        module = load_module()
        doc, statuses, run_worker = self._start_manual_request(module, "manual-retry-document")
        delayed = []
        self._patch(module, _schedule_delayed=lambda seconds, callback: delayed.append(callback))

        def fake_request_completion(prefix, suffix, settings, cache_key=None, regeneration=None, on_retry=None):
            on_retry()  # the OpenAI-compatible provider retried once
            return ""

        self._patch(module, request_completion=fake_request_completion)
        run_worker()

        generating, hint = statuses
        self.assertTrue(generating.ended)
        self.assertEqual(hint.texts, [module.REASONING_RETRY_STATUS_TEXT])
        self.assertFalse(hint.ended)  # held for the minimum visible time
        self.assertEqual(len(delayed), 1)
        delayed[0]()
        self.assertTrue(hint.ended)

    def test_manual_completion_without_retry_shows_no_hint(self):
        module = load_module()
        doc, statuses, run_worker = self._start_manual_request(module, "manual-no-retry-document")
        self._patch(module, request_completion=lambda *args, **kwargs: "")

        run_worker()

        self.assertEqual(len(statuses), 1)  # only "Generating..."
        self.assertTrue(statuses[0].ended)

    def test_manual_completion_hides_retry_hint_when_the_retry_fails(self):
        module = load_module()
        doc, statuses, run_worker = self._start_manual_request(module, "manual-retry-error-document")

        def failing_request_completion(*args, on_retry=None, **kwargs):
            on_retry()
            raise RuntimeError("Could not reach the server")

        self._patch(module, request_completion=failing_request_completion)
        run_worker()

        self.assertEqual(len(statuses), 2)
        self.assertTrue(all(status.ended for status in statuses))

    def test_cancelling_a_manual_request_hides_retry_hint_without_waiting_for_the_worker(self):
        module = load_module()
        doc, statuses, run_worker = self._start_manual_request(module, "manual-retry-cancel-document")
        ended_while_worker_blocked = []

        def fake_request_completion(*args, on_retry=None, **kwargs):
            on_retry()
            module._cancel_manual_request(doc)  # Esc while the retry is still running
            ended_while_worker_blocked.extend(status.ended for status in statuses)
            return ""

        self._patch(module, request_completion=fake_request_completion)
        run_worker()

        self.assertEqual(ended_while_worker_blocked, [True, True])

    def test_manual_revision_shows_retry_hint_until_the_request_is_done(self):
        module = load_module()
        doc, statuses, run_worker = self._start_manual_request(module, "revision-retry-document", revise=True)

        def fake_request_revision(selected, before, after, settings, regeneration=None, on_retry=None):
            on_retry()
            return ""

        self._patch(module, request_revision=fake_request_revision)
        run_worker()

        self.assertEqual(len(statuses), 2)
        self.assertEqual(statuses[1].texts, [module.REASONING_RETRY_STATUS_TEXT])
        self.assertTrue(all(status.ended for status in statuses))

    def _run_continuous_request(self, module, doc, key, fake_request_completion, status_factory):
        """Shared plumbing for the two continuous-indicator tests below."""
        settings = module.normalize_settings({})
        originals = {
            "_has_ghost": module._has_ghost,
            "_status_indicator": module._status_indicator,
            "_get_text_context": module._get_text_context,
            "_has_enough_new_words_for_continuous": module._has_enough_new_words_for_continuous,
            "_start_daemon_thread": module._start_daemon_thread,
            "request_completion": module.request_completion,
            # A real (but detached) pyuno module can be importable even
            # outside LibreOffice, in which case _post_to_ui queues onto an
            # AsyncCallback that nothing ever pumps. Forcing it synchronous
            # isolates the indicator wiring from that unrelated environment
            # quirk.
            "_post_to_ui": module._post_to_ui,
            # _ReasoningRetryIndicator.hide() defers status.end() by up to
            # REASONING_RETRY_MIN_VISIBLE_SECONDS via _schedule_delayed; run
            # it inline here so these wiring-focused tests don't depend on
            # timing (the delay itself is covered separately below).
            "_schedule_delayed": module._schedule_delayed,
        }
        scheduled = []
        module._has_ghost = lambda current_doc: False
        module._status_indicator = status_factory
        module._get_text_context = lambda current_doc, current_settings: (object(), "before", "")
        module._has_enough_new_words_for_continuous = lambda k, prefix: True
        module._start_daemon_thread = lambda target, args: scheduled.append((target, args))
        module._post_to_ui = lambda callback: callback()
        module._schedule_delayed = lambda seconds, callback: callback()
        module.request_completion = fake_request_completion
        try:
            started = module._start_continuous_request(doc, settings, force=True)
            self.assertTrue(started)
            self.assertEqual(len(scheduled), 1)
            target, args = scheduled[0]
            target(*args)
        finally:
            for name, value in originals.items():
                setattr(module, name, value)
            module._CONTINUOUS_REQUESTS.pop(key, None)

    def test_continuous_request_stays_silent_without_a_retry(self):
        module = load_module()

        class Doc:
            def getRuntimeUID(self):
                return "continuous-silent-document"

        created = []

        class Status:
            def __init__(self):
                self.started = False
                self.ended = False

            def start(self, *args):
                self.started = True

            def end(self):
                self.ended = True

        def status_factory(current_doc):
            created.append(Status())
            return created[-1]

        def fake_request_completion(prefix, suffix, current_settings, cache_key=None, on_retry=None):
            return "a continuation"  # succeeds on the first try, no retry

        self._run_continuous_request(
            module, Doc(), "continuous-silent-document", fake_request_completion, status_factory
        )

        # _status_indicator should never even be called when nothing needs
        # to retry: continuous suggestions stay completely silent.
        self.assertEqual(created, [])

    def test_continuous_request_shows_hint_only_when_retrying(self):
        module = load_module()

        class Doc:
            def getRuntimeUID(self):
                return "continuous-retry-document"

        class Status:
            def __init__(self):
                self.started = False
                self.ended = False
                self.texts = []

            def start(self, text, value):
                self.started = True
                self.texts.append(text)

            def end(self):
                self.ended = True

        status = Status()

        def fake_request_completion(prefix, suffix, current_settings, cache_key=None, on_retry=None):
            on_retry()  # simulate the OpenAI-compatible provider retrying once
            return "a continuation"

        self._run_continuous_request(
            module,
            Doc(),
            "continuous-retry-document",
            fake_request_completion,
            lambda current_doc: status,
        )

        self.assertTrue(status.started)
        self.assertTrue(status.ended)
        self.assertEqual(status.texts, [module.REASONING_RETRY_STATUS_TEXT])

    def test_reasoning_retry_indicator_ends_immediately_once_shown_long_enough(self):
        module = load_module()

        class Status:
            def __init__(self):
                self.ended = False

            def start(self, *args):
                pass

            def end(self):
                self.ended = True

        clock = [100.0, 100.0 + module.REASONING_RETRY_MIN_VISIBLE_SECONDS]
        delayed_calls = []
        originals = {
            "_status_indicator": module._status_indicator,
            "_monotonic": module._monotonic,
            "_schedule_delayed": module._schedule_delayed,
        }
        status = Status()
        module._status_indicator = lambda doc: status
        module._monotonic = lambda: clock.pop(0)
        module._schedule_delayed = lambda seconds, callback: delayed_calls.append((seconds, callback))
        try:
            indicator = module._ReasoningRetryIndicator(doc=object())
            indicator.show()
            indicator.hide()
        finally:
            for name, value in originals.items():
                setattr(module, name, value)

        # The hint was already visible for the full minimum by the time
        # hide() ran, so status.end() fires right away with no timer.
        self.assertEqual(delayed_calls, [])
        self.assertTrue(status.ended)

    def test_reasoning_retry_indicator_delays_hide_until_minimum_visible_time(self):
        module = load_module()

        class Status:
            def __init__(self):
                self.ended = False

            def start(self, *args):
                pass

            def end(self):
                self.ended = True

        # Only 0.3s have passed when hide() is called, well under the minimum.
        clock = [100.0, 100.3]
        delayed_calls = []
        originals = {
            "_status_indicator": module._status_indicator,
            "_monotonic": module._monotonic,
            "_schedule_delayed": module._schedule_delayed,
            "_post_to_ui": module._post_to_ui,
        }
        status = Status()
        module._status_indicator = lambda doc: status
        module._monotonic = lambda: clock.pop(0)
        module._schedule_delayed = lambda seconds, callback: delayed_calls.append((seconds, callback))
        module._post_to_ui = lambda callback: callback()
        try:
            indicator = module._ReasoningRetryIndicator(doc=object())
            indicator.show()
            indicator.hide()

            # Not ended yet: hide() deferred the real end() call instead of
            # letting the hint flash by after only 0.3s.
            self.assertFalse(status.ended)
            self.assertEqual(len(delayed_calls), 1)
            seconds, callback = delayed_calls[0]
            self.assertAlmostEqual(seconds, module.REASONING_RETRY_MIN_VISIBLE_SECONDS - 0.3, places=6)

            # Once the deferred timer actually fires, the indicator ends.
            callback()
            self.assertTrue(status.ended)
        finally:
            for name, value in originals.items():
                setattr(module, name, value)

    def test_reasoning_retry_indicator_hide_without_show_is_a_no_op(self):
        module = load_module()
        delayed_calls = []
        original = module._schedule_delayed
        module._schedule_delayed = lambda seconds, callback: delayed_calls.append((seconds, callback))
        try:
            indicator = module._ReasoningRetryIndicator(doc=object())
            indicator.hide()  # never shown - must not raise or schedule anything
        finally:
            module._schedule_delayed = original

        self.assertEqual(delayed_calls, [])

    def test_reasoning_retry_indicator_ignores_a_late_show_after_hide(self):
        module = load_module()
        statuses = self._patch_retry_hint_ui(module)
        indicator = module._ReasoningRetryIndicator(doc=object())

        indicator.hide()  # the request already finished...
        indicator.show()  # ...before the queued show() got to run

        self.assertEqual(statuses, [])

    def test_reasoning_retry_indicator_is_not_stuck_when_hide_runs_inside_start(self):
        module = load_module()
        delayed = []
        indicator = module._ReasoningRetryIndicator(doc=object())

        class Status:
            ended = False

            def start(self, text, value):
                # LibreOffice reschedules its event loop inside start(), which
                # can run the already queued response handler right here.
                indicator.hide()

            def end(self):
                self.ended = True

        status = Status()
        self._patch(
            module,
            _status_indicator=lambda doc: status,
            _schedule_delayed=lambda seconds, callback: delayed.append(callback),
            _post_to_ui=lambda callback: callback(),
        )

        indicator.show()

        self.assertEqual(len(delayed), 1)
        delayed[0]()
        self.assertTrue(status.ended)

    def test_reasoning_retry_indicator_hide_never_raises(self):
        module = load_module()
        statuses = self._patch_retry_hint_ui(module)

        def broken_timer(seconds, callback):
            raise RuntimeError("can't start new thread")

        self._patch(module, _schedule_delayed=broken_timer)
        indicator = module._ReasoningRetryIndicator(doc=object())
        indicator.show()

        indicator.hide()  # must not raise; ends the hint right away instead

        self.assertTrue(statuses[0].ended)

    def test_manual_completion_keeps_matching_text_typed_while_model_runs(self):
        module = load_module()

        class Doc:
            def getRuntimeUID(self):
                return "typing-during-request"

        class Status:
            def end(self):
                pass

        doc = Doc()
        shown = []
        originals = {
            "_get_text_context": module._get_text_context,
            "_show_ghost_completion": module._show_ghost_completion,
        }
        settings = module.normalize_settings({})
        request_id = module._begin_manual_request(
            doc,
            "typing-during-request",
            Status(),
            {
                "kind": "completion",
                "prefix": "The story",
                "settings": settings,
                "regeneration": None,
                "preview": True,
                "quiet": False,
            },
        )
        module._get_text_context = lambda current_doc, current_settings: (
            object(),
            "The story continued",
            "",
        )
        module._show_ghost_completion = lambda *args, **kwargs: shown.append((args, kwargs))
        try:
            module._handle_manual_completion_response(
                doc,
                "typing-during-request",
                request_id,
                "The story",
                " continued into the night",
                None,
            )
        finally:
            for name, value in originals.items():
                setattr(module, name, value)
            module._finish_manual_request("typing-during-request")

        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0][0][2], " into the night")

    def test_request_indicator_loops_without_replacing_text_and_restores_formatting(self):
        module = load_module()

        class Range:
            def __init__(self):
                self.text = module.REQUEST_INDICATOR_TEXT
                self.CharColor = 0x123456
                self.CharTransparence = module.REQUEST_INDICATOR_TRANSPARENCIES[0]
                self.replacements = []
                self.transparency_when_removed = None

            def getString(self):
                return self.text

            def setString(self, value):
                self.replacements.append(value)
                if value == "":
                    self.transparency_when_removed = self.CharTransparence
                self.text = value

            def getStart(self):
                return self

        text_range = Range()
        indicator = module.RequestIndicator(
            object(),
            text_range,
            {"CharColor": 0x123456, "CharTransparence": 0},
        )
        indicator._schedule_next_frame = lambda: None
        transparencies = []
        for _index in range(5):
            indicator._advance_frame()
            transparencies.append(text_range.CharTransparence)

        self.assertEqual(
            transparencies,
            [55, 0, 55, 0, 55],
        )
        self.assertEqual(text_range.getString(), module.REQUEST_INDICATOR_TEXT)
        self.assertEqual(text_range.replacements, [])
        self.assertEqual(text_range.CharColor, 0x123456)

        indicator.remove()
        self.assertEqual(text_range.getString(), "")
        self.assertEqual(text_range.replacements, [""])
        self.assertEqual(text_range.transparency_when_removed, 0)
        self.assertEqual(text_range.CharTransparence, 0)
        self.assertFalse(indicator.active)

    def test_escape_cancels_active_manual_request_and_removes_indicator(self):
        module = load_module()

        class Doc:
            def getRuntimeUID(self):
                return "escape-cancel-document"

        class Status:
            def __init__(self):
                self.ended = False

            def end(self):
                self.ended = True

        class Indicator:
            def __init__(self):
                self.removed = False

            def remove(self):
                self.removed = True

        class Event:
            KeyCode = module.ESCAPE
            Modifiers = 0
            KeyChar = ""

        doc = Doc()
        status = Status()
        indicator = Indicator()
        cancel_event = threading.Event()
        key = module._doc_key(doc)
        module._begin_manual_request(
            doc,
            key,
            status,
            {
                "kind": "completion",
                "indicator": indicator,
                "cancel_event": cancel_event,
            },
        )
        try:
            handler = module.LibreCompleteAIKeyHandler(doc)
            self.assertTrue(handler.keyPressed(Event()))
        finally:
            module._finish_manual_request(key)

        self.assertTrue(cancel_event.is_set())
        self.assertTrue(indicator.removed)
        self.assertTrue(status.ended)
        self.assertNotIn(key, module._MANUAL_REQUESTS)
        self.assertNotIn(key, module._BUSY_DOCS)

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

    def test_reconcile_completion_handles_a_full_rolling_context_window(self):
        module = load_module()
        request_prefix = "0123456789" * 30
        typed = " and then"
        current_prefix = request_prefix[len(typed) :] + typed
        state, remaining = module._reconcile_continuous_completion(
            request_prefix,
            current_prefix,
            " and then the door opened",
        )
        self.assertEqual(state, "match")
        self.assertEqual(remaining, " the door opened")

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
        original_prewarm = module._schedule_codex_session_prewarm
        module._message_box = lambda *args: messages.append(args)
        prewarmed = []
        module._schedule_codex_session_prewarm = lambda settings: prewarmed.append(settings)
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
            self.assertEqual(len(prewarmed), 1)
        finally:
            module._message_box = original_message_box
            module._schedule_codex_session_prewarm = original_prewarm

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

        original_prewarm = module._schedule_codex_session_prewarm
        module._schedule_codex_session_prewarm = lambda settings: None
        try:
            self.assertTrue(module.enable_autocomplete_for_doc(first_proxy))
            self.assertTrue(module.is_autocomplete_enabled(second_proxy))
            self.assertTrue(module.disable_autocomplete_for_doc(second_proxy))
            self.assertFalse(module.is_autocomplete_enabled(first_proxy))
        finally:
            module._schedule_codex_session_prewarm = original_prewarm

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

    def test_request_openai_retries_when_reasoning_exhausts_token_budget(self):
        module = load_module()
        payloads = []
        responses = [
            {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
            {"choices": [{"message": {"content": "the next line"}, "finish_reason": "stop"}]},
        ]

        def fake_post_json(url, payload, headers):
            payloads.append(dict(payload))
            return responses[len(payloads) - 1]

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings(
                {"openai_api_key": "sk-test", "openai_model": "gemma", "max_tokens": "48"}
            )
            self.assertEqual(module._request_openai("before", "after", settings), "the next line")
        finally:
            module._post_json = original

        self.assertEqual(len(payloads), 2)
        first_budget = payloads[0]["max_completion_tokens"]
        self.assertLess(first_budget * 4, module.OPENAI_TRUNCATION_RETRY_MIN_TOKENS)
        self.assertEqual(payloads[1]["max_completion_tokens"], module.OPENAI_TRUNCATION_RETRY_MIN_TOKENS)

    def test_request_openai_retry_quadruples_a_larger_budget(self):
        module = load_module()
        payloads = []
        responses = [
            {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
            {"choices": [{"message": {"content": "the next line"}, "finish_reason": "stop"}]},
        ]

        def fake_post_json(url, payload, headers):
            payloads.append(dict(payload))
            return responses[len(payloads) - 1]

        self._patch(module, _post_json=fake_post_json)
        settings = module.normalize_settings({"openai_api_key": "sk-test", "openai_model": "gemma"})

        module._request_openai_messages(module.build_messages("before", "", settings), settings, token_limit=100)

        self.assertEqual([payload["max_completion_tokens"] for payload in payloads], [100, 400])

    def test_request_openai_does_not_retry_when_content_present(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append(payload)
            return {"choices": [{"message": {"content": "ok"}, "finish_reason": "length"}]}

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings({"openai_api_key": "sk-test", "openai_model": "m"})
            self.assertEqual(module._request_openai("a", "b", settings), "ok")
        finally:
            module._post_json = original
        self.assertEqual(len(calls), 1)

    def test_request_openai_notifies_on_retry_only_when_retrying(self):
        module = load_module()
        retries = []
        responses = [
            {"choices": [{"message": {"content": "the next line"}, "finish_reason": "stop"}]},
            {"choices": [{"message": {"content": None}, "finish_reason": "length"}]},
            {"choices": [{"message": {"content": "the next line"}, "finish_reason": "stop"}]},
        ]
        self._patch(module, _post_json=lambda url, payload, headers: responses.pop(0))
        settings = module.normalize_settings({"openai_api_key": "sk-test", "openai_model": "gemma"})
        messages = module.build_messages("before", "", settings)

        module._request_openai_messages(messages, settings, on_retry=lambda: retries.append("first"))
        module._request_openai_messages(messages, settings, on_retry=lambda: retries.append("second"))

        self.assertEqual(retries, ["second"])
        self.assertEqual(responses, [])

    def test_request_openai_retries_even_if_the_retry_hook_fails(self):
        module = load_module()
        responses = [
            {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
            {"choices": [{"message": {"content": "the next line"}, "finish_reason": "stop"}]},
        ]
        self._patch(module, _post_json=lambda url, payload, headers: responses.pop(0))
        settings = module.normalize_settings({"openai_api_key": "sk-test", "openai_model": "gemma"})

        def broken_hook():
            raise RuntimeError("status bar unavailable")

        content = module._request_openai_messages(
            module.build_messages("before", "", settings), settings, on_retry=broken_hook
        )

        self.assertEqual(content, "the next line")

    def test_request_openai_retries_without_hint_when_reasoning_is_allowed(self):
        module = load_module()
        retries = []
        responses = [
            {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
            {"choices": [{"message": {"content": "the next line"}, "finish_reason": "stop"}]},
        ]
        self._patch(module, _post_json=lambda url, payload, headers: responses.pop(0))
        settings = module.normalize_settings(
            {"openai_api_key": "sk-test", "openai_model": "gemma", "allow_reasoning": "true"}
        )

        content = module._request_openai_messages(
            module.build_messages("before", "", settings), settings, on_retry=lambda: retries.append(True)
        )

        self.assertEqual(content, "the next line")
        self.assertEqual(responses, [])  # it still retried...
        self.assertEqual(retries, [])  # ...but did not suggest turning thinking off

    def test_request_openai_retries_when_inline_reasoning_is_cut_off(self):
        module = load_module()
        payloads = []
        responses = [
            {"choices": [{"message": {"content": "<think>The user wants me to continue"}, "finish_reason": "length"}]},
            {"choices": [{"message": {"content": "the next line"}, "finish_reason": "stop"}]},
        ]

        def fake_post_json(url, payload, headers):
            payloads.append(dict(payload))
            return responses[len(payloads) - 1]

        self._patch(module, _post_json=fake_post_json)
        settings = module.normalize_settings({"openai_api_key": "sk-test", "openai_model": "qwen3"})

        self.assertEqual(module._request_openai("before", "after", settings), "the next line")
        self.assertEqual(len(payloads), 2)

    def test_request_openai_does_not_retry_after_cancellation(self):
        module = load_module()
        cancel_event = threading.Event()
        payloads = []
        retries = []

        def fake_post_json(url, payload, headers):
            payloads.append(payload)
            cancel_event.set()  # the user pressed Escape while the model was thinking
            return {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}

        self._patch(module, _post_json=fake_post_json)
        settings = module.normalize_settings({"openai_api_key": "sk-test", "openai_model": "gemma"})
        module._REQUEST_CONTEXT.cancel_event = cancel_event
        try:
            with self.assertRaises(module._RequestCancelled):
                module._request_openai_messages(
                    module.build_messages("before", "", settings),
                    settings,
                    on_retry=lambda: retries.append(True),
                )
        finally:
            del module._REQUEST_CONTEXT.cancel_event

        self.assertEqual(len(payloads), 1)
        self.assertEqual(retries, [])

    def test_request_revision_retries_and_notifies_when_reasoning_exhausts_budget(self):
        module = load_module()
        retries = []
        responses = [
            {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
            {"choices": [{"message": {"content": "the cat"}, "finish_reason": "stop"}]},
        ]
        self._patch(module, _post_json=lambda url, payload, headers: responses.pop(0))
        settings = module.normalize_settings({"openai_api_key": "sk-test", "openai_model": "gemma"})

        revision = module.request_revision("teh cat", "", "", settings, on_retry=lambda: retries.append(True))

        self.assertEqual(revision, "the cat")
        self.assertEqual(retries, [True])

    def test_http_request_closes_active_response_when_cancelled(self):
        module = load_module()
        reading = threading.Event()
        closed = threading.Event()
        cancel_event = threading.Event()
        errors = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                self.close()

            def read(self):
                reading.set()
                closed.wait(2)
                raise OSError("response closed")

            def close(self):
                closed.set()

        original_urlopen = module.urllib.request.urlopen
        module.urllib.request.urlopen = lambda request, timeout: Response()

        def make_request():
            module._REQUEST_CONTEXT.cancel_event = cancel_event
            try:
                module._post_json("http://localhost/test", {"prompt": "continue"})
            except Exception as exc:
                errors.append(exc)
            finally:
                del module._REQUEST_CONTEXT.cancel_event

        worker = threading.Thread(target=make_request)
        try:
            worker.start()
            self.assertTrue(reading.wait(1))
            cancel_event.set()
            worker.join(2)
        finally:
            module.urllib.request.urlopen = original_urlopen

        self.assertFalse(worker.is_alive())
        self.assertTrue(closed.is_set())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], module._RequestCancelled)

    def test_request_codex_uses_saved_login_and_constrained_exec(self):
        module = load_module()
        calls = []

        class Result:
            returncode = 0
            stdout = '{"completion":"the next line"}\n'
            stderr = ""

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return Result()

        original_run = module.subprocess.run
        original_resolve = module._resolve_codex_executable
        original_codex_key = os.environ.get("CODEX_API_KEY")
        original_openai_key = os.environ.get("OPENAI_API_KEY")
        module.subprocess.run = fake_run
        module._resolve_codex_executable = lambda configured: "C:/Tools/codex.exe"
        os.environ["CODEX_API_KEY"] = "must-not-be-inherited"
        os.environ["OPENAI_API_KEY"] = "must-not-be-inherited"
        try:
            settings = module.normalize_settings(
                {
                    "provider": "codex",
                    "codex_executable": "C:/Tools/codex.exe",
                    "codex_model": "gpt-5.4",
                    "allow_reasoning": "false",
                }
            )
            completion = module.request_completion(
                "Wings thundered above Elian.",
                "This after-cursor text must not be sent.",
                settings,
            )
        finally:
            module.subprocess.run = original_run
            module._resolve_codex_executable = original_resolve
            if original_codex_key is None:
                os.environ.pop("CODEX_API_KEY", None)
            else:
                os.environ["CODEX_API_KEY"] = original_codex_key
            if original_openai_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = original_openai_key

        self.assertEqual(completion, "the next line")
        command, kwargs = calls[0]
        self.assertEqual(command[:2], ["C:/Tools/codex.exe", "exec"])
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--sandbox", command)
        self.assertIn("read-only", command)
        self.assertIn('web_search="disabled"', command)
        self.assertIn('model_reasoning_effort="low"', command)
        self.assertEqual(command[-1], "-")
        self.assertEqual(command[command.index("--model") + 1], "gpt-5.4")
        self.assertTrue(Path(command[command.index("--output-schema") + 1]).is_file())
        self.assertIn("Wings thundered above Elian.", kwargs["input"])
        self.assertNotIn("This after-cursor text must not be sent.", kwargs["input"])
        self.assertNotIn("CODEX_API_KEY", kwargs["env"])
        self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
        self.assertNotEqual(kwargs["cwd"], str(Path.cwd()))

    def test_request_codex_rejects_unstructured_output(self):
        module = load_module()
        with self.assertRaisesRegex(RuntimeError, "structured output"):
            module._extract_codex_completion("not json")

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

    def test_openai_compatibility_fallback_is_cached_for_later_requests(self):
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
                }
            )
            self.assertEqual(module._request_openai("before", "", settings), "fallback line")
            self.assertEqual(module._request_openai("before again", "", settings), "fallback line")
        finally:
            module._post_json = original

        self.assertEqual(len(calls), 3)
        self.assertIn("reasoning_effort", calls[0])
        self.assertNotIn("reasoning_effort", calls[1])
        self.assertNotIn("reasoning_effort", calls[2])

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
            settings = module.normalize_settings(
                {
                    "provider": "ollama",
                    "ollama_model": "qwen3",
                    "ollama_completion_mode": "raw",
                }
            )
            self.assertEqual(module._request_ollama("before", "after", settings), "the next line")
        finally:
            module._post_json = original

        self.assertTrue(calls[0][0].endswith("/api/generate"))
        self.assertIs(calls[0][1]["raw"], True)
        self.assertIs(calls[0][1]["think"], False)
        self.assertEqual(calls[0][1]["keep_alive"], "30m")
        self.assertEqual(calls[0][1]["prompt"], "before")
        self.assertEqual(calls[0][1]["options"]["num_ctx"], 2048)
        self.assertIn("Okay, let me", calls[0][1]["options"]["stop"])
        self.assertNotIn("\n\n", calls[0][1]["options"]["stop"])

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
        self.assertEqual(calls[0][1]["keep_alive"], "30m")
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
                {
                    "provider": "ollama",
                    "ollama_model": "qwen3",
                    "allow_reasoning": "true",
                    "ollama_completion_mode": "raw",
                }
            )
            self.assertEqual(module._request_ollama("before", "after", settings), "the next line")
        finally:
            module._post_json = original

        self.assertIs(calls[0][1]["think"], True)
        self.assertTrue(calls[0][0].endswith("/api/generate"))
        self.assertNotIn("Okay, let me", calls[0][1]["options"]["stop"])

    def test_ollama_regeneration_varies_sampling_but_not_output_budget(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append(payload)
            return {"response": "a different continuation"}

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings(
                {
                    "provider": "ollama",
                    "ollama_model": "qwen3",
                    "temperature": "0.45",
                    "prediction_words": "40",
                    "max_tokens": "96",
                    "ollama_completion_mode": "raw",
                }
            )
            module.request_completion(
                "before",
                "after",
                settings,
                regeneration={"attempt": 2, "rejected": ["old continuation"]},
            )
        finally:
            module._post_json = original

        self.assertAlmostEqual(calls[0]["options"]["temperature"], 0.61)
        self.assertEqual(calls[0]["options"]["num_predict"], 80)

    def test_ollama_guided_mode_uses_schema_and_ignores_suffix(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append((url, payload, headers))
            return {
                "message": {
                    "content": '{"completion":" the next seven useful words arrive right here"}'
                }
            }

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings(
                {
                    "provider": "ollama",
                    "ollama_model": "qwen3.5:4b",
                    "ollama_completion_mode": "guided",
                    "prediction_words": "7",
                    "max_tokens": "40",
                }
            )
            completion = module.request_completion("Before cursor", "After cursor", settings)
        finally:
            module._post_json = original

        self.assertEqual(completion, " the next seven useful words arrive right")
        self.assertEqual(len(calls), 1)
        url, payload, _headers = calls[0]
        self.assertTrue(url.endswith("/api/chat"))
        self.assertEqual(payload["format"], module.OLLAMA_COMPLETION_SCHEMA)
        self.assertEqual(payload["keep_alive"], "30m")
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["options"]["num_predict"], 26)
        self.assertEqual(payload["options"]["num_ctx"], 2048)
        joined = "\n".join(message["content"] for message in payload["messages"])
        self.assertIn("Before cursor", joined)
        self.assertNotIn("After cursor", joined)
        self.assertIn("current end of the document", joined)
        self.assertIn('"completion"', joined)
        self.assertTrue(joined.rstrip().endswith("/no_think"))

    def test_ollama_auto_falls_back_from_meta_completion_to_raw(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append(url)
            if url.endswith("/api/chat"):
                return {
                    "message": {
                        "content": '{"completion":"Okay, let me inspect what the user wants at the cursor."}'
                    }
                }
            return {"response": " a clean raw continuation"}

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings(
                {
                    "provider": "ollama",
                    "ollama_model": "weak-chat-model",
                    "ollama_completion_mode": "auto",
                }
            )
            completion = module.request_completion("before", "", settings)
        finally:
            module._post_json = original

        self.assertEqual(completion, " a clean raw continuation")
        self.assertEqual(
            calls,
            ["http://localhost:11434/api/chat", "http://localhost:11434/api/generate"],
        )

    def test_ollama_auto_caches_raw_after_first_guided_failure(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append(url)
            if url.endswith("/api/chat"):
                return {"message": {"content": "not json"}}
            return {"response": " raw fallback"}

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings(
                {
                    "provider": "ollama",
                    "ollama_model": "invalid-json-model",
                    "ollama_completion_mode": "auto",
                }
            )
            self.assertEqual(module.request_completion("before", "", settings), " raw fallback")
            self.assertEqual(module.request_completion("before", "", settings), " raw fallback")
            self.assertEqual(module.request_completion("before", "", settings), " raw fallback")
        finally:
            module._post_json = original

        self.assertEqual(
            calls,
            [
                "http://localhost:11434/api/chat",
                "http://localhost:11434/api/generate",
                "http://localhost:11434/api/generate",
                "http://localhost:11434/api/generate",
            ],
        )

    def test_ollama_auto_immediately_caches_unsupported_structured_output(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append(url)
            if url.endswith("/api/chat"):
                raise module._HttpJsonError(
                    400,
                    url,
                    '{"error":"structured format is not supported"}',
                )
            return {"response": " raw compatibility path"}

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings(
                {
                    "provider": "ollama",
                    "ollama_model": "legacy-model",
                    "ollama_completion_mode": "auto",
                }
            )
            self.assertEqual(
                module.request_completion("before", "", settings),
                " raw compatibility path",
            )
            self.assertEqual(
                module.request_completion("before", "", settings),
                " raw compatibility path",
            )
        finally:
            module._post_json = original

        self.assertEqual(
            calls,
            [
                "http://localhost:11434/api/chat",
                "http://localhost:11434/api/generate",
                "http://localhost:11434/api/generate",
            ],
        )

    def test_ollama_auto_uses_raw_fallback_even_with_text_after_cursor(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append(url)
            if url.endswith("/api/chat"):
                return {"message": {"content": "not json"}}
            return {"response": " forward continuation"}

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings(
                {
                    "provider": "ollama",
                    "ollama_model": "bad-at-structured-output",
                    "ollama_completion_mode": "auto",
                }
            )
            self.assertEqual(
                module.request_completion("before", "existing after", settings),
                " forward continuation",
            )
        finally:
            module._post_json = original

        self.assertEqual(
            calls,
            [
                "http://localhost:11434/api/chat",
                "http://localhost:11434/api/generate",
            ],
        )

    def test_ollama_guided_empty_completion_is_single_pass(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append(url)
            return {"message": {"content": '{"completion":""}'}}

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings(
                {"provider": "ollama", "ollama_completion_mode": "guided"}
            )
            self.assertEqual(module.request_completion("before", "existing after", settings), "")
        finally:
            module._post_json = original

        self.assertEqual(
            calls,
            ["http://localhost:11434/api/chat"],
        )

    def test_ollama_guided_mode_does_not_silently_fallback(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append(url)
            return {"message": {"content": "not json"}}

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings(
                {
                    "provider": "ollama",
                    "ollama_completion_mode": "guided",
                }
            )
            with self.assertRaises(module._GuidedCompletionError):
                module.request_completion("before", "after", settings)
        finally:
            module._post_json = original

        self.assertEqual(calls, ["http://localhost:11434/api/chat"])

    def test_ollama_gpt_oss_uses_supported_thinking_levels(self):
        module = load_module()
        disabled = module.normalize_settings({"ollama_model": "gpt-oss:20b"})
        allowed = module.normalize_settings(
            {"ollama_model": "gpt-oss:20b", "allow_reasoning": "true"}
        )
        self.assertEqual(module._ollama_think_setting(disabled), "low")
        self.assertEqual(module._ollama_think_setting(allowed), "medium")

    def test_ollama_context_window_scales_without_using_model_maximum(self):
        module = load_module()
        self.assertEqual(module._ollama_context_window("short prompt", 80), 2048)
        self.assertEqual(module._ollama_context_window("a" * 12000, 100), 4096)
        self.assertEqual(module._ollama_context_window("a" * 30000, 100), 16384)
        self.assertEqual(module._ollama_context_window("å" * 50000, 100), 16384)

    def test_openai_completion_ignores_text_after_cursor(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append(payload)
            return {
                "choices": [
                    {"message": {"content": "The dragon landed and spoke to Elian."}}
                ]
            }

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings(
                {
                    "provider": "openai",
                    "openai_api_key": "sk-test",
                    "openai_model": "gpt-5.4-mini",
                }
            )
            completion = module.request_completion(
                "Wings thundered above Elian.",
                "The dragon landed and fixed its gaze on him.",
                settings,
            )
        finally:
            module._post_json = original

        self.assertEqual(completion, "The dragon landed and spoke to Elian.")
        self.assertEqual(len(calls), 1)
        joined = "\n".join(message["content"] for message in calls[0]["messages"])
        self.assertNotIn("The dragon landed and fixed its gaze on him.", joined)

    def test_clean_completion_strips_labels_and_fences(self):
        module = load_module()
        text = "```text\nCompletion: the candle bent toward the draft.\n```"
        self.assertEqual(module.clean_completion(text), "the candle bent toward the draft.")

    def test_clean_completion_strips_visible_reasoning_blocks(self):
        module = load_module()
        text = "<think>I should continue with a sentence.</think>\nCompletion: the candle bent toward the draft."
        self.assertEqual(module.clean_completion(text), "the candle bent toward the draft.")

    def test_clean_completion_drops_reasoning_cut_off_by_token_budget(self):
        module = load_module()
        text = "<think>The user wants me to continue the sentence about"
        self.assertEqual(module.clean_completion(text, "The house was"), "")

    def test_clean_completion_keeps_literal_think_tags_in_prose(self):
        module = load_module()
        text = "wrap the reasoning in a <think> tag before answering."
        self.assertEqual(module.clean_completion(text, "Tell the model to"), text)

    def test_request_revision_keeps_literal_think_tags(self):
        module = load_module()
        revised = "Wrap the reasoning in a <think> tag before answering."
        self._patch(
            module,
            _post_json=lambda url, payload, headers: {
                "choices": [{"message": {"content": revised}, "finish_reason": "stop"}]
            },
        )
        settings = module.normalize_settings({"openai_api_key": "sk-test", "openai_model": "gemma"})

        revision = module.request_revision("Wrap teh reasoning in a <think> tag before answering.", "", "", settings)

        self.assertEqual(revision, revised)

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

    def test_clean_completion_does_not_remove_single_word_echo_into_fragment(self):
        module = load_module()
        prefix = "It was no ordinary creature"
        completion = "creature of ancient mind and voice."
        self.assertEqual(
            module.clean_completion(completion, prefix),
            "creature of ancient mind and voice.",
        )

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

    def test_go_right_chunks_long_distances(self):
        module = load_module()

        class Cursor:
            def __init__(self):
                self.calls = []

            def goRight(self, count, expand):
                self.calls.append((count, expand))
                return True

        cursor = Cursor()
        self.assertTrue(module._go_right(cursor, 65001, True))
        self.assertEqual(cursor.calls, [(32000, True), (32000, True), (1001, True)])

    def test_build_revision_messages_uses_selection_and_surrounding_context(self):
        module = load_module()
        settings = module.normalize_settings({})
        messages = module.build_revision_messages(
            "He walked into the forest unworried [Use a more fitting verb and adjective]",
            "Rain stopped at dawn.",
            "The birds had fallen silent.",
            settings,
        )
        system, prompt = (message["content"] for message in messages)

        self.assertIn("already correct, return it\nexactly unchanged", system)
        self.assertIn("private editing guidance", system)
        self.assertIn("Rain stopped at dawn.", prompt)
        self.assertIn("Use a more fitting verb and adjective", prompt)
        self.assertIn("The birds had fallen silent.", prompt)
        self.assertIn("<selection>", prompt)

    def test_revision_context_collects_at_most_ten_words_on_each_side(self):
        module = load_module()

        class Text:
            def __init__(self, value):
                self.value = value

            def createTextCursorByRange(self, text_range):
                return Range(self, text_range.start, text_range.end)

        class Range:
            def __init__(self, text, start, end=None):
                self.text = text
                self.start = start
                self.end = start if end is None else end

            def isCollapsed(self):
                return self.start == self.end

            def getText(self):
                return self.text

            def getStart(self):
                return Range(self.text, self.start)

            def getEnd(self):
                return Range(self.text, self.end)

            def getString(self):
                return self.text.value[self.start : self.end]

            def goLeft(self, count, expand):
                destination = max(0, self.start - count)
                if expand:
                    self.start = destination
                else:
                    self.start = destination
                    self.end = destination
                return destination == self.start - count

            def goRight(self, count, expand):
                destination = min(len(self.text.value), self.end + count)
                if expand:
                    self.end = destination
                else:
                    self.start = destination
                    self.end = destination
                return destination == self.end + count

        before_words = "one two three four five six seven eight nine ten eleven twelve"
        selected = "teh selected phrase"
        after_words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
        text = Text(f"{before_words} {selected} {after_words}")
        start = text.value.index(selected)
        doc = type("Doc", (), {"view_cursor": Range(text, start, start + len(selected))})()
        original_view_cursor = module._view_cursor
        module._view_cursor = lambda current_doc: current_doc.view_cursor
        try:
            _cursor, current, before, after = module._get_revision_context(doc)
        finally:
            module._view_cursor = original_view_cursor

        self.assertEqual(current, selected)
        self.assertEqual(before.split(), before_words.split()[-10:])
        self.assertEqual(after.split(), after_words.split()[:10])

    def test_request_revision_sends_after_selection_context_to_openai(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append(payload)
            return {"choices": [{"message": {"content": "He strolled into the forest confidently."}}]}

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings(
                {
                    "openai_api_key": "sk-test",
                    "max_tokens": "80",
                    "prediction_words": "3",
                }
            )
            revision = module.request_revision(
                "He walked into the forest unworried [Improve the word choice]",
                "The storm had finally passed.",
                "He whistled as he went.",
                settings,
            )
        finally:
            module._post_json = original

        self.assertEqual(revision, "He strolled into the forest confidently.")
        self.assertEqual(calls[0]["max_completion_tokens"], 80)
        prompt = "\n".join(message["content"] for message in calls[0]["messages"])
        self.assertIn("The storm had finally passed.", prompt)
        self.assertIn("He whistled as he went.", prompt)
        self.assertIn("Improve the word choice", prompt)

    def test_request_revision_uses_ollama_guided_schema(self):
        module = load_module()
        calls = []

        def fake_post_json(url, payload, headers):
            calls.append((url, payload))
            return {"message": {"content": '{"completion":"A corrected phrase."}'}}

        original = module._post_json
        module._post_json = fake_post_json
        try:
            settings = module.normalize_settings(
                {
                    "provider": "ollama",
                    "ollama_completion_mode": "guided",
                    "max_tokens": "80",
                }
            )
            revision = module.request_revision(
                "A corected phrase.",
                "The opening sentence.",
                "The next sentence.",
                settings,
            )
        finally:
            module._post_json = original

        self.assertEqual(revision, "A corrected phrase.")
        self.assertTrue(calls[0][0].endswith("/api/chat"))
        self.assertEqual(calls[0][1]["format"], module.OLLAMA_COMPLETION_SCHEMA)
        prompt = "\n".join(message["content"] for message in calls[0][1]["messages"])
        self.assertIn("The opening sentence.", prompt)
        self.assertIn("The next sentence.", prompt)

    def test_request_revision_routes_through_codex(self):
        module = load_module()
        calls = []
        original = module._request_codex_messages
        module._request_codex_messages = lambda messages, settings: (
            calls.append(messages) or "A corrected phrase."
        )
        try:
            revision = module.request_revision(
                "A corected phrase.",
                "The opening sentence.",
                "The next sentence.",
                module.normalize_settings({"provider": "codex"}),
            )
        finally:
            module._request_codex_messages = original

        self.assertEqual(revision, "A corrected phrase.")
        prompt = "\n".join(message["content"] for message in calls[0])
        self.assertIn("The opening sentence.", prompt)
        self.assertIn("The next sentence.", prompt)

    def test_clean_revision_preserves_an_unchanged_selection_exactly(self):
        module = load_module()
        selected = "  This sentence is correct.  "
        self.assertEqual(module._clean_revision("This sentence is correct.", selected), selected)

    def test_clean_revision_removes_echoed_square_bracket_guidance(self):
        module = load_module()
        selected = "He walked [Use a more fitting verb]"
        self.assertEqual(
            module._clean_revision("He strolled [Use a more fitting verb]", selected),
            "He strolled",
        )

    def test_rejected_revision_remembers_candidate_for_a_distinct_retry(self):
        module = load_module()

        class Range:
            def __init__(self, value):
                self.value = value

            def getString(self):
                return self.value

            def setString(self, value):
                self.value = value

            def getStart(self):
                return self

            def getEnd(self):
                return self

        class Doc:
            def getRuntimeUID(self):
                return "revision-retry-document"

        doc = Doc()
        key = module._doc_key(doc)
        original_select = module._select_view_range
        original_restore = module._restore_insertion_properties
        module._select_view_range = lambda *args: None
        module._restore_insertion_properties = lambda *args: None
        try:
            ghost = module.GhostRevision(
                doc,
                Range("He walked."),
                "He walked.",
                Range("He strolled."),
                "He strolled.",
                {},
                "revision-context",
            )
            module._GHOSTS[key] = ghost
            self.assertTrue(module._reject_ghost(doc))
            regeneration = module._revision_regeneration_for_context(key, "revision-context")
            prompt = module.build_revision_messages(
                "He walked.", "Before", "After", module.normalize_settings({}), regeneration
            )[1]["content"]
        finally:
            module._select_view_range = original_select
            module._restore_insertion_properties = original_restore
            module._GHOSTS.pop(key, None)
            module._REVISION_REGENERATION_STATES.pop(key, None)

        self.assertEqual(ghost.revision_range.getString(), "")
        self.assertEqual(regeneration["attempt"], 1)
        self.assertEqual(regeneration["rejected"], ["He strolled."])
        self.assertIn("genuinely different revision", prompt)
        self.assertIn("He strolled.", prompt)

    def test_accepting_a_revision_replaces_the_selection_and_removes_preview(self):
        module = load_module()

        class Range:
            def __init__(self, value):
                self.value = value

            def getString(self):
                return self.value

            def setString(self, value):
                self.value = value

            def getEnd(self):
                return self

        class Doc:
            pass

        doc = Doc()
        source = Range("He walked.")
        preview = Range("He strolled.")
        original_move = module._move_view_cursor_to_range
        original_restore = module._restore_insertion_properties
        module._move_view_cursor_to_range = lambda *args: None
        module._restore_insertion_properties = lambda *args: None
        try:
            ghost = module.GhostRevision(
                doc,
                source,
                "He walked.",
                preview,
                "He strolled.",
                {},
                "revision-context",
            )
            ghost.accept()
        finally:
            module._move_view_cursor_to_range = original_move
            module._restore_insertion_properties = original_restore

        self.assertEqual(source.getString(), "He strolled.")
        self.assertEqual(preview.getString(), "")

    def test_discarding_a_revision_for_typing_keeps_original_text(self):
        module = load_module()

        class Range:
            def __init__(self, value):
                self.value = value

            def getString(self):
                return self.value

            def setString(self, value):
                self.value = value

            def getEnd(self):
                return self

        doc = object()
        source = Range("He walked.")
        preview = Range("He strolled.")
        moved_to = []
        original_move = module._move_view_cursor_to_range
        original_restore = module._restore_insertion_properties
        module._move_view_cursor_to_range = lambda current_doc, text_range: moved_to.append(text_range)
        module._restore_insertion_properties = lambda *args: None
        try:
            ghost = module.GhostRevision(
                doc,
                source,
                "He walked.",
                preview,
                "He strolled.",
                {},
                "revision-context",
            )
            ghost.discard(preserve_selection=False)
        finally:
            module._move_view_cursor_to_range = original_move
            module._restore_insertion_properties = original_restore

        self.assertEqual(source.getString(), "He walked.")
        self.assertEqual(preview.getString(), "")
        self.assertEqual(moved_to, [source])

    def test_tab_routes_selected_text_to_revision(self):
        module = load_module()

        class Event:
            KeyCode = module.TAB
            Modifiers = 0
            KeyChar = ""

        calls = []
        originals = {
            "_has_ghost": module._has_ghost,
            "_has_selected_text": module._has_selected_text,
            "revise_current_selection": module.revise_current_selection,
            "complete_current_position": module.complete_current_position,
        }
        module._has_ghost = lambda doc: False
        module._has_selected_text = lambda doc: True
        module.revise_current_selection = lambda doc, preview=True: calls.append(("revision", preview))
        module.complete_current_position = lambda doc, preview=True: calls.append(("completion", preview))
        try:
            self.assertTrue(module.LibreCompleteAIKeyHandler(object()).keyPressed(Event()))
        finally:
            for name, value in originals.items():
                setattr(module, name, value)

        self.assertEqual(calls, [("revision", True)])


if __name__ == "__main__":
    unittest.main()
