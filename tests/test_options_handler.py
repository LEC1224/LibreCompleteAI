import importlib.util
import os
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
        settings = module.normalize_settings({"provider": "bad"})
        self.assertEqual(settings["provider"], "openai")
        self.assertEqual(settings["continuous_suggestions"], "false")
        self.assertEqual(settings["max_context_words"], "600")

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
                self.controls = {"continuous_suggestions": Control()}

            def getControl(self, name):
                return self.controls[name]

        window = Window()
        module._set_control_bool(window, "continuous_suggestions", "true")
        self.assertEqual(module._get_control_bool(window, "continuous_suggestions"), "true")
        module._set_control_bool(window, "continuous_suggestions", "false")
        self.assertEqual(module._get_control_bool(window, "continuous_suggestions"), "false")


if __name__ == "__main__":
    unittest.main()
