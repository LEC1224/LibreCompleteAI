import json
import os

try:
    import unohelper
    from com.sun.star.awt import XAdjustmentListener, XContainerWindowEventHandler
    from com.sun.star.lang import XServiceInfo
except Exception:
    class _UnoHelper:
        class Base(object):
            pass

        class ImplementationHelper(object):
            def addImplementation(self, *args):
                pass

    unohelper = _UnoHelper()

    class XContainerWindowEventHandler(object):
        pass

    class XAdjustmentListener(object):
        pass

    class XServiceInfo(object):
        pass


SERVICE_NAME = "org.codex.librecompleteai.OptionsEventHandler"
DEFAULT_SETTINGS = {
    "provider": "openai",
    "openai_api_key": "",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_model": "gpt-4.1-mini",
    "ollama_host": "http://localhost:11434",
    "ollama_model": "llama3.2",
    "temperature": "0.45",
    "max_tokens": "96",
    "prediction_words": "24",
    "continuous_suggestions": "false",
    "max_context_words": "600",
    "prefix_chars": "2400",
    "suffix_chars": "600",
    "writing_guidance": "Match the author's language, tone, point of view, and pacing.",
}
SETTINGS_FIELDS = (
    "provider",
    "openai_api_key",
    "openai_base_url",
    "openai_model",
    "ollama_host",
    "ollama_model",
    "temperature",
    "max_tokens",
    "prediction_words",
    "continuous_suggestions",
    "max_context_words",
    "prefix_chars",
    "suffix_chars",
    "writing_guidance",
)
BOOLEAN_SETTINGS = ("continuous_suggestions",)
SLIDER_SETTINGS = {
    "max_context_words": "max_context_words_slider",
    "prediction_words": "prediction_words_slider",
}


def _config_dir():
    base = (
        os.environ.get("APPDATA")
        or os.environ.get("XDG_CONFIG_HOME")
        or os.path.join(os.path.expanduser("~"), ".config")
    )
    return os.path.join(base, "LibreCompleteAI")


def settings_path():
    return os.path.join(_config_dir(), "settings.json")


def normalize_settings(settings):
    merged = dict(DEFAULT_SETTINGS)
    if settings:
        merged.update(settings)

    provider = str(merged.get("provider", "openai")).strip().lower()
    if provider not in ("openai", "ollama"):
        provider = "openai"
    merged["provider"] = provider

    for key in SETTINGS_FIELDS:
        merged[key] = str(merged.get(key, DEFAULT_SETTINGS[key])).strip()

    return merged


def load_settings():
    try:
        with open(settings_path(), "r", encoding="utf-8") as handle:
            return normalize_settings(json.load(handle))
    except Exception:
        return normalize_settings({})


def save_settings(settings):
    settings = normalize_settings(settings)
    os.makedirs(os.path.dirname(settings_path()), exist_ok=True)
    with open(settings_path(), "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2, sort_keys=True)
    return settings


class LibreCompleteAIOptionsEventHandler(unohelper.Base, XServiceInfo, XContainerWindowEventHandler):
    def __init__(self, ctx):
        self.ctx = ctx
        self.ImplementationName = SERVICE_NAME
        self.services = (SERVICE_NAME,)
        self.listeners = []

    def callHandlerMethod(self, window, event_object, method):
        if method == "external_event":
            return self.handleExternalEvent(window, event_object)
        return False

    def getSupportedMethodNames(self):
        return ("external_event",)

    def handleExternalEvent(self, window, event_object):
        if event_object in ("initialize", "back"):
            self.loadData(window)
        elif event_object == "ok":
            self.saveData(window)
        return True

    def loadData(self, window):
        self.listeners = []
        settings = load_settings()
        for name in SETTINGS_FIELDS:
            value = settings.get(name, DEFAULT_SETTINGS.get(name, ""))
            if name in BOOLEAN_SETTINGS:
                _set_control_bool(window, name, value)
            else:
                _set_control_text(window, name, value)
            _set_linked_slider(window, name, value)
        self._connect_slider_listeners(window)

    def saveData(self, window):
        settings = load_settings()
        for name in SETTINGS_FIELDS:
            if name in BOOLEAN_SETTINGS:
                settings[name] = _get_control_bool(window, name, settings.get(name, "false"))
            else:
                settings[name] = _get_control_text(window, name, settings.get(name, ""))
        save_settings(settings)

    def _connect_slider_listeners(self, window):
        if self.listeners:
            return
        for setting_name, slider_name in SLIDER_SETTINGS.items():
            try:
                slider = window.getControl(slider_name)
                target = window.getControl(setting_name)
                listener = _SliderToTextListener(target)
                slider.addAdjustmentListener(listener)
                self.listeners.append(listener)
            except Exception:
                pass

    def getImplementationName(self):
        return self.ImplementationName

    def supportsService(self, service_name):
        return service_name in self.services

    def getSupportedServiceNames(self):
        return self.services


def _set_control_text(window, name, value):
    try:
        control = window.getControl(name)
    except Exception:
        return

    value = str(value)
    for setter in (
        lambda: control.setText(value),
        lambda: setattr(control, "Text", value),
        lambda: setattr(control.getModel(), "Text", value),
        lambda: setattr(control.getModel(), "Value", int(float(value))),
    ):
        try:
            setter()
            return
        except Exception:
            pass


def _get_control_text(window, name, default=""):
    try:
        control = window.getControl(name)
    except Exception:
        return default

    for getter in (
        lambda: control.getText(),
        lambda: control.Text,
        lambda: control.getModel().Text,
        lambda: control.Value,
        lambda: control.getModel().Value,
    ):
        try:
            return str(getter())
        except Exception:
            pass
    return default


def _set_control_bool(window, name, value):
    try:
        control = window.getControl(name)
    except Exception:
        return

    state = 1 if _is_true(value) else 0
    for setter in (
        lambda: control.setState(state),
        lambda: setattr(control, "State", state),
        lambda: setattr(control.getModel(), "State", state),
    ):
        try:
            setter()
            return
        except Exception:
            pass


def _get_control_bool(window, name, default="false"):
    try:
        control = window.getControl(name)
    except Exception:
        return default

    for getter in (
        lambda: control.getState(),
        lambda: control.State,
        lambda: control.getModel().State,
    ):
        try:
            return "true" if int(getter()) != 0 else "false"
        except Exception:
            pass
    return "true" if _is_true(default) else "false"


def _set_linked_slider(window, name, value):
    slider_name = SLIDER_SETTINGS.get(name)
    if not slider_name:
        return
    try:
        slider = window.getControl(slider_name)
        slider.getModel().ScrollValue = int(float(value))
    except Exception:
        pass


class _SliderToTextListener(unohelper.Base, XAdjustmentListener):
    def __init__(self, target):
        self.target = target

    def adjustmentValueChanged(self, event):
        value = _slider_event_value(event)
        if value is not None and self.target is not None:
            _set_control_text_on_control(self.target, str(int(value)))

    def disposing(self, event):
        self.target = None


def _set_control_text_on_control(control, value):
    value = str(value)
    for setter in (
        lambda: control.setText(value),
        lambda: setattr(control, "Text", value),
        lambda: setattr(control.getModel(), "Text", value),
        lambda: setattr(control.getModel(), "Value", int(float(value))),
    ):
        try:
            setter()
            return
        except Exception:
            pass


def _slider_event_value(event):
    for getter in (
        lambda: event.Value,
        lambda: event.Source.getValue(),
        lambda: event.Source.getModel().ScrollValue,
    ):
        try:
            return getter()
        except Exception:
            pass
    return None


def _is_true(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on", "enabled")


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    LibreCompleteAIOptionsEventHandler,
    SERVICE_NAME,
    (SERVICE_NAME,),
)
