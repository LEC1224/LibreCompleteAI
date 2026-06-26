import json
import os
import re
import urllib.error
import urllib.request

try:
    import uno
    import unohelper
    from com.sun.star.awt import XKeyHandler
    from com.sun.star.awt.FontSlant import ITALIC
    from com.sun.star.awt.Key import ESCAPE, TAB
except Exception:
    uno = None

    class _UnoHelper:
        class Base(object):
            pass

    unohelper = _UnoHelper()

    class XKeyHandler(object):
        pass

    ESCAPE = 1281
    TAB = 1282
    ITALIC = "ITALIC"


EXTENSION_NAME = "LibreCompleteAI"
GHOST_COLOR = 0x9AA0A6
GHOST_PROPERTIES = ("CharColor", "CharPosture", "CharTransparence")
DEFAULT_SETTINGS = {
    "provider": "openai",
    "openai_api_key": "",
    "openai_base_url": "https://api.openai.com/v1",
    "openai_model": "gpt-4.1-mini",
    "ollama_host": "http://localhost:11434",
    "ollama_model": "llama3.2",
    "temperature": "0.45",
    "max_tokens": "96",
    "prefix_chars": "2400",
    "suffix_chars": "600",
    "writing_guidance": "Match the author's language, tone, point of view, and pacing.",
}

SYSTEM_PROMPT = """You are an inline autocomplete engine for prose in LibreOffice Writer.
Return only text that should be inserted at the cursor.
Do not explain, label, quote, or wrap the completion.
Do not repeat text that is already before the cursor.
Prefer a concise continuation: a phrase, clause, sentence, or short paragraph.
Preserve the author's language, tone, tense, person, and formatting conventions.
If no helpful continuation is possible, return an empty string."""

_HANDLERS = {}
_BUSY_DOCS = set()
_GHOSTS = {}


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

    for key in (
        "openai_api_key",
        "openai_base_url",
        "openai_model",
        "ollama_host",
        "ollama_model",
        "temperature",
        "max_tokens",
        "prefix_chars",
        "suffix_chars",
        "writing_guidance",
    ):
        merged[key] = str(merged.get(key, DEFAULT_SETTINGS[key])).strip()

    if not merged["openai_api_key"]:
        merged["openai_api_key"] = os.environ.get("OPENAI_API_KEY", "")

    return merged


def load_settings():
    path = settings_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return normalize_settings(json.load(handle))
    except FileNotFoundError:
        return normalize_settings({})
    except Exception:
        return normalize_settings({})


def save_settings(settings):
    settings = normalize_settings(settings)
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2, sort_keys=True)
    return settings


def _int_setting(settings, key):
    try:
        value = int(str(settings.get(key, DEFAULT_SETTINGS[key])).strip())
    except Exception:
        value = int(DEFAULT_SETTINGS[key])
    return max(0, value)


def _float_setting(settings, key):
    try:
        value = float(str(settings.get(key, DEFAULT_SETTINGS[key])).strip())
    except Exception:
        value = float(DEFAULT_SETTINGS[key])
    return max(0.0, min(2.0, value))


def build_messages(prefix, suffix, settings):
    max_words = max(12, _int_setting(settings, "max_tokens"))
    guidance = settings.get("writing_guidance") or DEFAULT_SETTINGS["writing_guidance"]
    user_prompt = (
        "Continue the document at <cursor>.\n\n"
        "Writing guidance:\n"
        f"{guidance}\n\n"
        "Text before <cursor>:\n"
        "<before>\n"
        f"{prefix}\n"
        "</before>\n\n"
        "Text after <cursor>:\n"
        "<after>\n"
        f"{suffix}\n"
        "</after>\n\n"
        f"Return only the text to insert at <cursor>. Keep it under about {max_words} words."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def clean_completion(text, prefix=""):
    if not text:
        return ""

    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"^\s*```[a-zA-Z0-9_-]*\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    text = text.strip("\n")

    stripped = text.strip()
    for label in ("Completion:", "Suggestion:", "Insert:", "Continuation:"):
        if stripped.lower().startswith(label.lower()):
            text = stripped[len(label) :].lstrip()
            stripped = text.strip()
            break

    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ("'", '"'):
        text = stripped[1:-1]

    text = _remove_prefix_echo(prefix, text)
    return text.rstrip()


def _remove_prefix_echo(prefix, completion):
    if not prefix or not completion:
        return completion

    tail = prefix[-240:]
    max_size = min(len(tail), len(completion), 120)
    for size in range(max_size, 7, -1):
        if tail[-size:] == completion[:size]:
            return completion[size:]
    return completion


def request_completion(prefix, suffix, settings):
    settings = normalize_settings(settings)
    if settings["provider"] == "ollama":
        raw = _request_ollama(prefix, suffix, settings)
    else:
        raw = _request_openai(prefix, suffix, settings)
    return clean_completion(raw, prefix)


def _request_openai(prefix, suffix, settings):
    api_key = settings.get("openai_api_key") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OpenAI provider is selected, but no API key is configured.")

    model = settings.get("openai_model")
    if not model:
        raise RuntimeError("OpenAI provider is selected, but no model label is configured.")

    base_url = (settings.get("openai_base_url") or DEFAULT_SETTINGS["openai_base_url"]).rstrip("/")
    payload = {
        "model": model,
        "messages": build_messages(prefix, suffix, settings),
        "temperature": _float_setting(settings, "temperature"),
        "max_tokens": _int_setting(settings, "max_tokens"),
    }
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }
    response = _post_json(base_url + "/chat/completions", payload, headers)
    try:
        return response["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError("OpenAI response did not include a chat completion.")


def _request_ollama(prefix, suffix, settings):
    model = settings.get("ollama_model")
    if not model:
        raise RuntimeError("Ollama provider is selected, but no model label is configured.")

    host = (settings.get("ollama_host") or DEFAULT_SETTINGS["ollama_host"]).rstrip("/")
    payload = {
        "model": model,
        "messages": build_messages(prefix, suffix, settings),
        "stream": False,
        "options": {
            "temperature": _float_setting(settings, "temperature"),
            "num_predict": _int_setting(settings, "max_tokens"),
        },
    }
    response = _post_json(host + "/api/chat", payload, {"Content-Type": "application/json"})
    try:
        return response["message"]["content"]
    except Exception:
        raise RuntimeError("Ollama response did not include a chat message.")


def _post_json(url, payload, headers=None, timeout=90):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body[:1000]}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}")

    try:
        return json.loads(body)
    except Exception:
        raise RuntimeError(f"Response from {url} was not JSON.")


def _current_document():
    script_context = globals().get("XSCRIPTCONTEXT")
    if not script_context:
        raise RuntimeError("LibreOffice script context is not available.")
    return script_context.getDocument()


def _component_context():
    script_context = globals().get("XSCRIPTCONTEXT")
    if script_context:
        return script_context.getComponentContext()
    if uno:
        return uno.getComponentContext()
    return None


def _controller(doc):
    try:
        return doc.getCurrentController()
    except Exception:
        return doc.CurrentController


def _view_cursor(doc):
    controller = _controller(doc)
    try:
        return controller.getViewCursor()
    except Exception:
        return controller.ViewCursor


def _is_writer_document(doc):
    try:
        return doc.supportsService("com.sun.star.text.TextDocument")
    except Exception:
        return False


def _doc_key(doc):
    return str(id(doc))


def _get_text_context(doc, settings):
    view_cursor = _view_cursor(doc)
    if not _cursor_is_collapsed(view_cursor):
        raise RuntimeError("Place the cursor where you want autocomplete; selected text is not supported.")

    text = view_cursor.getText()

    prefix_cursor = text.createTextCursorByRange(view_cursor.getStart())
    prefix_cursor.goLeft(_int_setting(settings, "prefix_chars"), True)
    prefix = prefix_cursor.getString()

    suffix_cursor = text.createTextCursorByRange(view_cursor.getEnd())
    suffix_cursor.goRight(_int_setting(settings, "suffix_chars"), True)
    suffix = suffix_cursor.getString()

    return view_cursor, prefix, suffix


def _insert_completion(view_cursor, completion):
    text = view_cursor.getText()
    text.insertString(view_cursor, completion, True)


def _cursor_is_collapsed(cursor):
    try:
        return cursor.isCollapsed()
    except Exception:
        try:
            return cursor.getString() == ""
        except Exception:
            return True


class GhostCompletion:
    def __init__(self, doc, text_range, completion, original_properties):
        self.doc = doc
        self.text_range = text_range
        self.completion = completion
        self.original_properties = original_properties

    def matches_document(self):
        try:
            return self.text_range.getString() == self.completion
        except Exception:
            return False

    def accept(self):
        if self.matches_document():
            _apply_properties(self.text_range, self.original_properties)
            _move_view_cursor_to_range(self.doc, self.text_range.getEnd())

    def discard(self):
        if not self.matches_document():
            return

        start = self.text_range.getStart()
        self.text_range.setString("")
        _move_view_cursor_to_range(self.doc, start)


def _has_ghost(doc):
    return _doc_key(doc) in _GHOSTS


def _accept_ghost(doc):
    ghost = _GHOSTS.pop(_doc_key(doc), None)
    if ghost:
        ghost.accept()
        return True
    return False


def _discard_ghost(doc):
    ghost = _GHOSTS.pop(_doc_key(doc), None)
    if ghost:
        ghost.discard()
        return True
    return False


def _show_ghost_completion(doc, view_cursor, completion):
    _discard_ghost(doc)

    text = view_cursor.getText()
    original_properties = _capture_properties(view_cursor, GHOST_PROPERTIES)

    text.insertString(view_cursor, completion, True)
    ghost_range = text.createTextCursorByRange(view_cursor.getEnd())
    _go_left(ghost_range, len(completion), True)
    _apply_ghost_style(ghost_range)
    _move_view_cursor_to_range(doc, ghost_range.getStart())

    _GHOSTS[_doc_key(doc)] = GhostCompletion(doc, ghost_range, completion, original_properties)


def _capture_properties(text_range, names):
    values = {}
    for name in names:
        try:
            values[name] = getattr(text_range, name)
        except Exception:
            pass
    return values


def _apply_properties(text_range, values):
    for name, value in values.items():
        try:
            setattr(text_range, name, value)
        except Exception:
            pass


def _apply_ghost_style(text_range):
    try:
        text_range.CharColor = GHOST_COLOR
    except Exception:
        pass
    try:
        text_range.CharPosture = ITALIC
    except Exception:
        pass
    try:
        text_range.CharTransparence = 25
    except Exception:
        pass


def _move_view_cursor_to_range(doc, text_range):
    try:
        _view_cursor(doc).gotoRange(text_range, False)
    except Exception:
        pass


def _go_left(cursor, count, expand):
    remaining = max(0, int(count))
    while remaining:
        step = min(remaining, 32000)
        if not cursor.goLeft(step, expand):
            return False
        remaining -= step
        expand = True
    return True


def complete_current_position(doc=None, preview=True):
    doc = doc or _current_document()
    if not _is_writer_document(doc):
        _message_box(doc, EXTENSION_NAME, "Open a Writer document before using autocomplete.")
        return

    if _has_ghost(doc):
        _accept_ghost(doc)
        return

    key = _doc_key(doc)
    if key in _BUSY_DOCS:
        return

    settings = load_settings()
    _BUSY_DOCS.add(key)
    status = _status_indicator(doc)
    try:
        status.start("Generating writing autocomplete...", 0)
        view_cursor, prefix, suffix = _get_text_context(doc, settings)
        completion = request_completion(prefix, suffix, settings)
        if completion:
            if preview:
                _show_ghost_completion(doc, view_cursor, completion)
            else:
                _insert_completion(view_cursor, completion)
        else:
            _message_box(doc, EXTENSION_NAME, "The model did not return a useful continuation.")
    except Exception as exc:
        _message_box(doc, EXTENSION_NAME, str(exc))
    finally:
        try:
            status.end()
        except Exception:
            pass
        _BUSY_DOCS.discard(key)


class LibreCompleteAIKeyHandler(unohelper.Base, XKeyHandler):
    def __init__(self, doc):
        self.doc = doc

    def keyPressed(self, event):
        try:
            key_code = getattr(event, "KeyCode", None)
            modifiers = getattr(event, "Modifiers", 0)

            if _has_ghost(self.doc):
                if key_code == TAB and modifiers == 0:
                    _accept_ghost(self.doc)
                    return True
                if key_code == ESCAPE and modifiers == 0:
                    _discard_ghost(self.doc)
                    return True
                _discard_ghost(self.doc)
                return False

            if key_code == TAB and modifiers == 0:
                complete_current_position(self.doc, preview=True)
                return True
        except Exception as exc:
            _message_box(self.doc, EXTENSION_NAME, str(exc))
            return True
        return False

    def keyReleased(self, event):
        return False

    def disposing(self, event):
        self.doc = None


def enable_autocomplete(*args):
    doc = _current_document()
    if not _is_writer_document(doc):
        _message_box(doc, EXTENSION_NAME, "Open a Writer document before enabling autocomplete.")
        return

    key = _doc_key(doc)
    if key in _HANDLERS:
        _message_box(doc, EXTENSION_NAME, "LibreCompleteAI is already enabled for this Writer window.")
        return

    controller = _controller(doc)
    handler = LibreCompleteAIKeyHandler(doc)
    controller.addKeyHandler(handler)
    _HANDLERS[key] = (controller, handler)
    _message_box(doc, EXTENSION_NAME, "LibreCompleteAI is enabled for this Writer window.")


def disable_autocomplete(*args):
    doc = _current_document()
    key = _doc_key(doc)
    if key not in _HANDLERS:
        _message_box(doc, EXTENSION_NAME, "LibreCompleteAI is not enabled for this Writer window.")
        return

    controller, handler = _HANDLERS.pop(key)
    try:
        _discard_ghost(doc)
        controller.removeKeyHandler(handler)
    finally:
        _message_box(doc, EXTENSION_NAME, "LibreCompleteAI is disabled for this Writer window.")


def toggle_autocomplete(*args):
    doc = _current_document()
    if _doc_key(doc) in _HANDLERS:
        disable_autocomplete()
    else:
        enable_autocomplete()


def complete_now(*args):
    doc = _current_document()
    complete_current_position(doc, preview=_doc_key(doc) in _HANDLERS)


def show_settings(*args):
    doc = _current_document()
    ctx = _component_context()
    if not ctx:
        raise RuntimeError("LibreOffice component context is not available.")

    settings = load_settings()
    dialog = _create_settings_dialog(ctx, settings)
    try:
        if dialog.execute() == 1:
            updated = _settings_from_dialog(dialog, settings)
            save_settings(updated)
            _message_box(doc, EXTENSION_NAME, "Settings saved.")
    finally:
        dialog.dispose()


def _create_settings_dialog(ctx, settings):
    smgr = ctx.ServiceManager
    model = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialogModel", ctx)
    model.PositionX = 80
    model.PositionY = 80
    model.Width = 250
    model.Height = 245
    model.Title = "LibreCompleteAI Settings"

    y = 10
    _label(model, "provider_label", 10, y, 70, "Provider")
    provider = _control(model, "com.sun.star.awt.UnoControlListBoxModel", "provider", 88, y - 2, 145, 14)
    provider.Dropdown = True
    provider.StringItemList = ("OpenAI", "Ollama")
    provider.SelectedItems = (0 if settings["provider"] == "openai" else 1,)

    y += 22
    _label(model, "api_key_label", 10, y, 70, "OpenAI key")
    api_key = _edit(model, "openai_api_key", 88, y - 2, 145, settings.get("openai_api_key", ""))
    api_key.EchoChar = 42

    y += 18
    _label(model, "base_url_label", 10, y, 70, "OpenAI base URL")
    _edit(model, "openai_base_url", 88, y - 2, 145, settings.get("openai_base_url", ""))

    y += 18
    _label(model, "openai_model_label", 10, y, 70, "OpenAI model")
    _edit(model, "openai_model", 88, y - 2, 145, settings.get("openai_model", ""))

    y += 22
    _label(model, "ollama_host_label", 10, y, 70, "Ollama host")
    _edit(model, "ollama_host", 88, y - 2, 145, settings.get("ollama_host", ""))

    y += 18
    _label(model, "ollama_model_label", 10, y, 70, "Ollama model")
    _edit(model, "ollama_model", 88, y - 2, 145, settings.get("ollama_model", ""))

    y += 22
    _label(model, "temperature_label", 10, y, 70, "Temperature")
    _edit(model, "temperature", 88, y - 2, 45, settings.get("temperature", ""))

    _label(model, "max_tokens_label", 143, y, 42, "Max tokens")
    _edit(model, "max_tokens", 188, y - 2, 45, settings.get("max_tokens", ""))

    y += 18
    _label(model, "prefix_label", 10, y, 70, "Before chars")
    _edit(model, "prefix_chars", 88, y - 2, 45, settings.get("prefix_chars", ""))

    _label(model, "suffix_label", 143, y, 42, "After chars")
    _edit(model, "suffix_chars", 188, y - 2, 45, settings.get("suffix_chars", ""))

    y += 22
    _label(model, "guidance_label", 10, y, 70, "Writing guidance")
    guidance = _edit(model, "writing_guidance", 88, y - 2, 145, settings.get("writing_guidance", ""))
    guidance.Height = 34
    guidance.MultiLine = True
    guidance.VScroll = True

    _button(model, "ok", 138, 220, 45, "OK", 1)
    _button(model, "cancel", 188, 220, 45, "Cancel", 2)

    dialog = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialog", ctx)
    dialog.setModel(model)
    toolkit = smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", ctx)
    dialog.createPeer(toolkit, None)
    return dialog


def _settings_from_dialog(dialog, current):
    provider_control = dialog.getControl("provider")
    provider = provider_control.getSelectedItem().lower()
    updated = dict(current)
    updated["provider"] = provider
    for name in (
        "openai_api_key",
        "openai_base_url",
        "openai_model",
        "ollama_host",
        "ollama_model",
        "temperature",
        "max_tokens",
        "prefix_chars",
        "suffix_chars",
        "writing_guidance",
    ):
        updated[name] = dialog.getControl(name).getText()
    return normalize_settings(updated)


def _control(model, service, name, x, y, width, height):
    control = model.createInstance(service)
    control.PositionX = x
    control.PositionY = y
    control.Width = width
    control.Height = height
    model.insertByName(name, control)
    return control


def _label(model, name, x, y, width, text):
    label = _control(model, "com.sun.star.awt.UnoControlFixedTextModel", name, x, y, width, 10)
    label.Label = text
    return label


def _edit(model, name, x, y, width, text):
    edit = _control(model, "com.sun.star.awt.UnoControlEditModel", name, x, y, width, 12)
    edit.Text = text
    return edit


def _button(model, name, x, y, width, text, button_type):
    button = _control(model, "com.sun.star.awt.UnoControlButtonModel", name, x, y, width, 14)
    button.Label = text
    button.PushButtonType = button_type
    return button


def _status_indicator(doc):
    class NullStatus:
        def start(self, text, value):
            pass

        def end(self):
            pass

    try:
        frame = _controller(doc).getFrame()
        return frame.createStatusIndicator()
    except Exception:
        return NullStatus()


def _message_box(doc, title, message):
    try:
        ctx = _component_context()
        smgr = ctx.ServiceManager
        toolkit = smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", ctx)
        parent = _controller(doc).getFrame().getContainerWindow()
        box = toolkit.createMessageBox(parent, "infobox", 1, title, str(message))
        box.execute()
    except Exception:
        print(f"{title}: {message}")


g_exportedScripts = (
    show_settings,
    enable_autocomplete,
    disable_autocomplete,
    toggle_autocomplete,
    complete_now,
)
