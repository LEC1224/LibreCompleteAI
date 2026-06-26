import json
import importlib.util
import os

try:
    import uno
    import unohelper
    from com.sun.star.awt import XAdjustmentListener, XContainerWindowEventHandler
    from com.sun.star.frame import FeatureStateEvent, XDispatch, XDispatchProvider
    from com.sun.star.lang import XInitialization, XServiceInfo
except Exception:
    uno = None

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

    class XDispatch(object):
        pass

    class XDispatchProvider(object):
        pass

    class FeatureStateEvent(object):
        pass

    class XServiceInfo(object):
        pass

    class XInitialization(object):
        pass


SERVICE_NAME = "org.codex.librecompleteai.OptionsEventHandler"
DISPATCH_SERVICE_NAME = "org.codex.librecompleteai.Dispatch"
DISPATCH_PROTOCOL = "vnd.librecompleteai:"
SUPPORTED_DISPATCH_COMMANDS = ("toggle", "continuous", "complete")
_RUNTIME_MODULE = None
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


class LibreCompleteAIDispatch(unohelper.Base, XServiceInfo, XInitialization, XDispatchProvider, XDispatch):
    def __init__(self, ctx):
        self.ctx = ctx
        self.ImplementationName = DISPATCH_SERVICE_NAME
        self.services = (DISPATCH_SERVICE_NAME, "com.sun.star.frame.ProtocolHandler")
        self.listeners = []
        self.frame = None

    def initialize(self, args):
        for arg in _flatten_initialization_args(args):
            if arg is not None:
                self.frame = arg
                return

    def queryDispatch(self, url, target_frame_name, search_flags):
        if _url_command(url) in SUPPORTED_DISPATCH_COMMANDS:
            return self
        return None

    def queryDispatches(self, requests):
        return tuple(
            self.queryDispatch(request.FeatureURL, request.FrameName, request.SearchFlags)
            for request in requests
        )

    def dispatch(self, url, args):
        command = _url_command(url)
        runtime = _load_writer_runtime()
        doc = self._current_writer_document(runtime)
        if doc is None:
            self._notify_all()
            return

        if command == "toggle":
            runtime.toggle_autocomplete_for_doc(doc)
        elif command == "continuous":
            runtime.toggle_continuous_suggestions_for_doc(doc)
        elif command == "complete":
            runtime.complete_current_position(doc, preview=runtime.is_autocomplete_enabled(doc))
        self._notify_all()

    def addStatusListener(self, listener, url):
        self.listeners.append((listener, url))
        self._notify_listener(listener, url)

    def removeStatusListener(self, listener, url):
        self.listeners = [
            (current_listener, current_url)
            for current_listener, current_url in self.listeners
            if current_listener is not listener or _url_command(current_url) != _url_command(url)
        ]

    def _notify_all(self):
        for listener, url in list(self.listeners):
            self._notify_listener(listener, url)

    def _notify_listener(self, listener, url):
        try:
            event = FeatureStateEvent()
            event.Source = self
            event.FeatureURL = url
            event.FeatureDescriptor = ""
            event.IsEnabled = _url_command(url) in SUPPORTED_DISPATCH_COMMANDS and self._has_writer_document()
            event.Requery = False
            event.State = _uno_boolean(self._state_for_url(url))
            listener.statusChanged(event)
        except Exception:
            pass

    def _state_for_url(self, url):
        command = _url_command(url)
        runtime = _load_writer_runtime()
        doc = self._current_writer_document(runtime)
        if command == "toggle":
            return bool(doc is not None and runtime.is_autocomplete_enabled(doc))
        if command == "continuous":
            return bool(runtime.is_continuous_suggestions_enabled())
        return False

    def _has_writer_document(self):
        try:
            runtime = _load_writer_runtime()
            return self._current_writer_document(runtime) is not None
        except Exception:
            return False

    def _current_writer_document(self, runtime):
        for candidate in (
            self._document_from_frame(self.frame),
            self._document_from_desktop_frame(),
            self._document_from_desktop_component(),
        ):
            if runtime._is_writer_document(candidate):
                return candidate
        return None

    def _document_from_frame(self, frame):
        frame = _unwrap_initialization_value(frame)
        if frame is None:
            return None
        try:
            if frame.supportsService("com.sun.star.text.TextDocument"):
                return frame
        except Exception:
            pass
        try:
            controller = frame.getController()
            return controller.getModel()
        except Exception:
            pass
        try:
            controller = frame.getCurrentController()
            return controller.getModel()
        except Exception:
            return None

    def _document_from_desktop_frame(self):
        try:
            desktop = self.ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", self.ctx)
            return self._document_from_frame(desktop.getCurrentFrame())
        except Exception:
            return None

    def _document_from_desktop_component(self):
        try:
            desktop = self.ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", self.ctx)
            doc = desktop.getCurrentComponent()
        except Exception:
            return None
        return doc

    def getImplementationName(self):
        return self.ImplementationName

    def supportsService(self, service_name):
        return service_name in self.services

    def getSupportedServiceNames(self):
        return self.services


def _load_writer_runtime():
    global _RUNTIME_MODULE
    if _RUNTIME_MODULE is not None:
        return _RUNTIME_MODULE

    script_path = os.path.join(os.path.dirname(__file__), "Scripts", "python", "writer_autocomplete.py")
    spec = importlib.util.spec_from_file_location("_librecompleteai_writer_runtime", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _RUNTIME_MODULE = module
    return module


def _url_command(url):
    for attr in ("Path", "Name"):
        try:
            value = str(getattr(url, attr) or "").strip()
            if value:
                return value.lstrip("/")
        except Exception:
            pass

    try:
        complete = str(url.Complete)
    except Exception:
        complete = str(url or "")
    if complete.startswith(DISPATCH_PROTOCOL):
        complete = complete[len(DISPATCH_PROTOCOL) :]
    elif ":" in complete:
        complete = complete.split(":", 1)[1]
    return complete.split("?", 1)[0].lstrip("/")


def _flatten_initialization_args(args):
    try:
        iterator = iter(args)
    except Exception:
        return (args,)

    flattened = []
    for arg in iterator:
        if isinstance(arg, (tuple, list)):
            flattened.extend(_unwrap_initialization_value(value) for value in arg)
        else:
            flattened.append(_unwrap_initialization_value(arg))
    return tuple(flattened)


def _unwrap_initialization_value(value):
    try:
        if hasattr(value, "value"):
            value = value.value
    except Exception:
        pass
    try:
        name = str(value.Name)
        if name.lower() in ("frame", "xframe", "document", "model"):
            return value.Value
    except Exception:
        pass
    return value


def _uno_boolean(value):
    if uno is not None:
        try:
            return uno.Any("boolean", bool(value))
        except Exception:
            pass
    return bool(value)


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
g_ImplementationHelper.addImplementation(
    LibreCompleteAIDispatch,
    DISPATCH_SERVICE_NAME,
    (DISPATCH_SERVICE_NAME, "com.sun.star.frame.ProtocolHandler"),
)
