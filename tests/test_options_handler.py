import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "extension" / "options_handler.py"


def load_module():
    spec = importlib.util.spec_from_file_location("options_handler", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OptionsHandlerTests(unittest.TestCase):
    def test_control_text_helpers(self):
        module = load_module()

        class Control:
            def __init__(self):
                self.text = ""

            def setText(self, value):
                self.text = value

            def getText(self):
                return self.text

        class Window:
            def __init__(self):
                self.controls = {"provider": Control()}

            def getControl(self, name):
                return self.controls[name]

        window = Window()
        module._set_control_text(window, "provider", "ollama")
        self.assertEqual(module._get_control_text(window, "provider"), "ollama")

    def test_normalize_settings_rejects_unknown_provider(self):
        module = load_module()
        settings = module.normalize_settings({"provider": "bad", "suffix_chars": "999"})
        self.assertEqual(settings["provider"], "openai")
        self.assertNotIn("suffix_chars", settings)
        self.assertEqual(settings["autocomplete_enabled"], "false")
        self.assertEqual(settings["continuous_suggestions"], "false")
        self.assertEqual(settings["allow_reasoning"], "false")
        self.assertEqual(settings["ollama_model"], "qwen3.5:4b")
        self.assertEqual(settings["ollama_completion_mode"], "auto")
        self.assertEqual(settings["max_context_words"], "600")
        self.assertEqual(settings["reference_files"], "")

    def test_normalize_settings_preserves_internal_autocomplete_state(self):
        module = load_module()
        self.assertEqual(
            module.normalize_settings({"autocomplete_enabled": "yes"})[
                "autocomplete_enabled"
            ],
            "true",
        )

    def test_normalize_settings_keeps_only_unique_text_reference_files(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canon = root / "canon.md"
            characters = root / "characters.txt"
            settings = module.normalize_settings(
                {
                    "reference_files": "\n".join(
                        (str(canon), str(characters), str(canon), str(root / "image.png"))
                    )
                }
            )

        self.assertEqual(
            settings["reference_files"].splitlines(),
            [str(canon.resolve()), str(characters.resolve())],
        )

    def test_options_handler_supports_reference_file_buttons(self):
        module = load_module()
        handler = module.LibreCompleteAIOptionsEventHandler(ctx=None)
        self.assertEqual(
            handler.getSupportedMethodNames(),
            ("external_event", "add_reference_files", "clear_reference_files"),
        )

    def test_normalize_settings_validates_ollama_completion_mode(self):
        module = load_module()
        self.assertEqual(
            module.normalize_settings({"ollama_completion_mode": "RAW"})["ollama_completion_mode"],
            "raw",
        )
        self.assertEqual(
            module.normalize_settings({"ollama_completion_mode": "invalid"})[
                "ollama_completion_mode"
            ],
            "auto",
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

    def test_normalize_settings_canonicalizes_localized_numeric_fields(self):
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

    def test_checkbox_helpers_roundtrip_boolean_strings(self):
        module = load_module()

        class Control:
            def __init__(self):
                self.state = 0

            def setState(self, value):
                self.state = value

            def getState(self):
                return self.state

        class Window:
            def __init__(self):
                self.controls = {
                    "continuous_suggestions": Control(),
                    "allow_reasoning": Control(),
                }

            def getControl(self, name):
                return self.controls[name]

        window = Window()
        module._set_control_bool(window, "continuous_suggestions", "true")
        self.assertEqual(module._get_control_bool(window, "continuous_suggestions"), "true")
        module._set_control_bool(window, "continuous_suggestions", "false")
        self.assertEqual(module._get_control_bool(window, "continuous_suggestions"), "false")
        module._set_control_bool(window, "allow_reasoning", "true")
        self.assertEqual(module._get_control_bool(window, "allow_reasoning"), "true")

    def test_secondary_toolbar_commands_follow_main_toggle_state(self):
        module = load_module()
        dispatch = module.LibreCompleteAIDispatch(ctx=None)

        class Url:
            Complete = "vnd.librecompleteai:continuous"

        class Listener:
            def __init__(self):
                self.events = []

            def statusChanged(self, event):
                self.events.append(event)

        listener = Listener()
        dispatch._has_writer_document = lambda: True
        dispatch._state_for_url = lambda url: True

        dispatch._is_autocomplete_enabled = lambda: False
        dispatch._notify_listener(listener, Url())
        self.assertEqual(len(listener.events), 1)
        self.assertFalse(listener.events[0].IsEnabled)
        self.assertFalse(listener.events[0].State.bVisible)

        listener.events = []
        dispatch._is_autocomplete_enabled = lambda: True
        dispatch._notify_listener(listener, Url())
        self.assertEqual(len(listener.events), 2)
        self.assertTrue(listener.events[0].IsEnabled)
        self.assertTrue(listener.events[0].State.bVisible)
        self.assertTrue(listener.events[1].IsEnabled)
        self.assertTrue(listener.events[1].State)

    def test_status_listener_restores_saved_state_before_first_toolbar_update(self):
        module = load_module()
        dispatch = module.LibreCompleteAIDispatch(ctx=None)
        calls = []
        dispatch._restore_saved_autocomplete_state = lambda: calls.append("restore")
        dispatch._notify_listener = lambda listener, url: calls.append("notify")

        dispatch.addStatusListener(object(), object())

        self.assertEqual(calls, ["restore", "notify"])


if __name__ == "__main__":
    unittest.main()
