import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request

try:
    import uno
    import unohelper
    from com.sun.star.awt import XAdjustmentListener, XKeyHandler
    from com.sun.star.awt.FontSlant import ITALIC
    from com.sun.star.awt.Key import ESCAPE, RIGHT, TAB
    from com.sun.star.awt.KeyModifier import MOD1
    try:
        from com.sun.star.awt.KeyModifier import MOD3
    except Exception:
        MOD3 = 8
except Exception:
    uno = None

    class _UnoHelper:
        class Base(object):
            pass

    unohelper = _UnoHelper()

    class XKeyHandler(object):
        pass

    class XAdjustmentListener(object):
        pass

    ESCAPE = 1281
    RIGHT = 1027
    TAB = 1282
    MOD1 = 2
    MOD3 = 8
    ITALIC = "ITALIC"


EXTENSION_NAME = "LibreCompleteAI"
EXTENSION_IDENTIFIER = "org.codex.librecompleteai"
GHOST_COLOR = 0x9AA0A6
GHOST_PROPERTIES = ("CharColor", "CharPosture", "CharTransparence")
NO_SPACE_BEFORE_COMPLETION_START = ".,;:!?)]}%"
NO_SPACE_AFTER_PREFIX_END = "([{/"
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
    "allow_reasoning": "false",
    "max_context_words": "600",
    "prefix_chars": "2400",
    "suffix_chars": "600",
    "writing_guidance": "Match the author's language, tone, point of view, and pacing.",
}

SYSTEM_PROMPT = """You are an inline autocomplete engine for prose in LibreOffice Writer.
Return only text that should be inserted at the cursor.
Do not explain, label, quote, or wrap the completion.
Do not think step by step, reason out loud, or include analysis.
Do not output <think> blocks or hidden reasoning.
Do not mention the user, prompt, cursor, model, task, instructions, or word count.
Do not write prefaces such as "Okay", "Let me", "Here is", or "Possible continuation".
Do not repeat text that is already before the cursor.
Prefer a concise continuation: a phrase, clause, sentence, or short paragraph.
Preserve the author's language, tone, tense, person, and formatting conventions.
If no helpful continuation is possible, return an empty string."""

SUMMARY_PROMPT = """You compress prose context for an inline writing autocomplete engine.
Preserve the facts, names, chronology, unresolved questions, tone, language, and point of view.
Do not continue the story or document.
Return only a compact summary of the provided earlier context."""

_HANDLERS = {}
_BUSY_DOCS = set()
_GHOSTS = {}
_CONTEXT_SUMMARIES = {}
_REGENERATION_STATES = {}
_LAST_AUTO_REQUEST = {}
_LAST_AUTO_PREFIX = {}
_CONTINUOUS_REQUESTS = {}
_CONTINUOUS_TIMERS = {}
_CONTINUOUS_LOCK = threading.RLock()
_CONTINUOUS_SEQUENCE = 0
_CONTINUOUS_TIMER_SEQUENCE = 0
_DIALOG_LISTENERS = {}
CONTINUOUS_MIN_INTERVAL_SECONDS = 0.75
CONTINUOUS_IDLE_DELAY_SECONDS = 0.85
CONTINUOUS_TRIGGER_WORDS = 3
SUMMARY_REFRESH_WORDS = 120
SUMMARY_RECENT_MIN_WORDS = 80
SUMMARY_RECENT_RATIO = 0.5
MAX_CONTEXT_CHAR_CAP = 80000
MAX_REJECTED_COMPLETIONS = 3
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
    "allow_reasoning",
    "max_context_words",
    "prefix_chars",
    "suffix_chars",
    "writing_guidance",
)
INTEGER_SETTINGS = (
    "max_tokens",
    "prediction_words",
    "max_context_words",
    "prefix_chars",
    "suffix_chars",
)
BOOLEAN_SETTINGS = ("continuous_suggestions", "allow_reasoning")
SLIDER_SETTINGS = {
    "max_context_words": {
        "slider": "max_context_words_slider",
        "minimum": 100,
        "maximum": 4000,
        "step": 100,
    },
    "prediction_words": {
        "slider": "prediction_words_slider",
        "minimum": 3,
        "maximum": 120,
        "step": 1,
    },
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

    for key in INTEGER_SETTINGS:
        merged[key] = str(_parse_integer(merged[key], DEFAULT_SETTINGS[key]))
    merged["temperature"] = _format_number(
        _parse_localized_number(merged["temperature"], DEFAULT_SETTINGS["temperature"])
    )

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
    return _parse_integer(settings.get(key, DEFAULT_SETTINGS[key]), DEFAULT_SETTINGS[key])


def _float_setting(settings, key):
    value = _parse_localized_number(settings.get(key, DEFAULT_SETTINGS[key]), DEFAULT_SETTINGS[key])
    return max(0.0, min(2.0, value))


def _parse_integer(value, default):
    return max(0, int(_parse_localized_number(value, default)))


def _parse_localized_number(value, default):
    """Parse settings written using either decimal commas or decimal points."""
    text = str(value).strip().replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
    if not text:
        text = str(default)

    if "," in text and "." in text:
        decimal_separator = "," if text.rfind(",") > text.rfind(".") else "."
        grouping_separator = "." if decimal_separator == "," else ","
        text = text.replace(grouping_separator, "")
        if decimal_separator == ",":
            text = text.replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")

    try:
        number = float(text)
    except Exception:
        number = float(str(default).replace(",", "."))
    if not math.isfinite(number):
        number = float(str(default).replace(",", "."))
    return number


def _format_number(value):
    return format(value, ".15g")


def _bool_setting(settings, key):
    value = str(settings.get(key, DEFAULT_SETTINGS.get(key, ""))).strip().lower()
    return value in ("1", "true", "yes", "on", "enabled")


def _prediction_words(settings):
    config = SLIDER_SETTINGS["prediction_words"]
    return _clamp(_int_setting(settings, "prediction_words"), config["minimum"], config["maximum"])


def _completion_token_limit(settings):
    token_cap = _int_setting(settings, "max_tokens")
    # A provider-side budget tied to the requested word count makes short
    # predictions stop near their target. The explicit token cap remains the
    # absolute ceiling for unusually token-dense text and reasoning models.
    word_budget = int(math.ceil(_prediction_words(settings) * 2.0))
    return max(8, min(max(8, token_cap), word_budget))


def _summary_token_limit(settings):
    max_context_words = max(100, _int_setting(settings, "max_context_words"))
    return max(96, min(512, max_context_words // 2))


def build_messages(prefix, suffix, settings, regeneration=None):
    max_words = _prediction_words(settings)
    min_words = max(1, int(math.floor(max_words * 0.8)))
    guidance = settings.get("writing_guidance") or DEFAULT_SETTINGS["writing_guidance"]
    reasoning_instruction = _reasoning_instruction(settings)
    regeneration_instruction = _regeneration_instruction(regeneration, max_words)
    user_prompt = (
        "Continue the document at <cursor>.\n\n"
        "Writing guidance:\n"
        f"{guidance}\n\n"
        f"{reasoning_instruction}\n\n"
        f"{regeneration_instruction}"
        "Text before <cursor>:\n"
        "<before>\n"
        f"{prefix}\n"
        "</before>\n\n"
        "Text after <cursor>:\n"
        "<after>\n"
        f"{suffix}\n"
        "</after>\n\n"
        f"Return only the text to insert at <cursor>. Start directly with document text. "
        f"Aim for {max_words} words; when the document can be continued naturally, use {min_words}-{max_words} words. "
        f"Never exceed {max_words} words."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def _regeneration_instruction(regeneration, target_words):
    if not regeneration or not regeneration.get("attempt"):
        return ""

    attempt = max(1, int(regeneration.get("attempt", 1)))
    directions = (
        "Take a different narrative or argumentative direction from the rejected attempt.",
        "Try a fresh angle with a noticeably different sentence structure and emphasis.",
        "Prefer a less obvious but still seamless next idea, image, detail, or action.",
    )
    instruction = directions[(attempt - 1) % len(directions)]
    rejected = [str(item).strip() for item in regeneration.get("rejected", ()) if str(item).strip()]
    rejected_block = ""
    if rejected:
        rejected_lines = "\n".join(f"- {item[:500]}" for item in rejected[-MAX_REJECTED_COMPLETIONS:])
        rejected_block = f"\nDo not repeat these rejected suggestions:\n{rejected_lines}"
    return (
        f"Regeneration attempt {attempt}: {instruction} "
        f"Keep the requested length near {target_words} words; do not shorten it merely because this is a retry."
        f"{rejected_block}\n\n"
    )


def build_summary_messages(text, settings):
    guidance = settings.get("writing_guidance") or DEFAULT_SETTINGS["writing_guidance"]
    target_words = max(40, min(180, _int_setting(settings, "max_context_words") // 4))
    reasoning_instruction = _reasoning_instruction(settings)
    user_prompt = (
        "Summarize this earlier document context for later inline autocomplete.\n\n"
        "Writing guidance:\n"
        f"{guidance}\n\n"
        f"{reasoning_instruction}\n\n"
        "Earlier context:\n"
        "<context>\n"
        f"{text}\n"
        "</context>\n\n"
        f"Keep the summary under about {target_words} words."
    )
    return [
        {"role": "system", "content": SUMMARY_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def clean_completion(text, prefix="", max_words=None):
    if not text:
        return ""

    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = _strip_reasoning_blocks(text)
    text = re.sub(r"^\s*```[a-zA-Z0-9_-]*\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    text = text.strip("\n")
    text = _extract_labeled_completion(text)

    stripped = text.strip()
    for label in ("Completion:", "Suggestion:", "Insert:", "Continuation:", "Possible continuation:"):
        if stripped.lower().startswith(label.lower()):
            text = stripped[len(label) :].lstrip()
            stripped = text.strip()
            break

    text = _strip_outer_quotes(text)

    if _looks_like_meta_response(text):
        return ""

    text = _remove_prefix_echo(prefix, text)
    text = _limit_completion_words(text, max_words)
    return text.rstrip()


def _reasoning_instruction(settings):
    if _bool_setting(settings, "allow_reasoning"):
        return "Reasoning is allowed internally if the selected model needs it, but return only the final insertable text."
    return "Do not reason, deliberate, explain, or think step by step. Produce only the next text."


def _strip_reasoning_blocks(text):
    text = re.sub(r"(?is)<think>.*?</think>\s*", "", text)
    text = re.sub(r"(?is)^\s*(analysis|reasoning|thoughts?)\s*:\s*.*?(?=\n\s*(final|completion|suggestion)\s*:|\Z)", "", text)
    return text


def _extract_labeled_completion(text):
    matches = list(
        re.finditer(
            r"(?im)(?:^|\n)\s*(?:possible\s+)?(?:completion|suggestion|continuation|insert|final(?: answer)?|output)\s*:\s*",
            text,
        )
    )
    if not matches:
        return text
    return _strip_outer_quotes(text[matches[-1].end() :].lstrip())


def _strip_outer_quotes(text):
    stripped = text.strip()
    for opener, closer in (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’")):
        if stripped.startswith(opener):
            end = stripped.find(closer, len(opener))
            if end > 0:
                return stripped[len(opener) : end]
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ("'", '"', "“", "”", "‘", "’"):
        return stripped[1:-1]
    return text


def _looks_like_meta_response(text):
    stripped = text.strip()
    if not stripped:
        return False
    lower = stripped[:1600].lower()
    strong_starts = (
        "let me",
        "i need",
        "i should",
        "i will",
        "i'll",
        "first,",
        "hmm",
        "wait,",
        "the user wants",
        "the task",
        "the text before",
        "the cursor",
    )
    if lower.startswith(strong_starts):
        return True

    markers = (
        "the user wants",
        "the task is",
        "the cursor",
        "text before the cursor",
        "provided text",
        "return only",
        "insert at <cursor>",
        "possible continuation",
        "let me count",
        "word count",
        "i need to",
        "i should",
    )
    if lower.startswith(("okay,", "ok,", "sure,")) and any(marker in lower for marker in markers):
        return True
    return sum(1 for marker in markers if marker in lower) >= 2


def _limit_completion_words(text, max_words):
    if max_words is None:
        return text
    try:
        max_words = int(max_words)
    except Exception:
        return text
    if max_words <= 0:
        return ""
    matches = list(re.finditer(r"\S+", text or ""))
    if len(matches) <= max_words:
        return text
    return text[: matches[max_words - 1].end()]


def _remove_prefix_echo(prefix, completion):
    if not prefix or not completion:
        return completion

    tail = prefix[-240:]
    max_size = min(len(tail), len(completion), 120)
    for size in range(max_size, 7, -1):
        if tail[-size:] == completion[:size]:
            return completion[size:]
    return completion


def request_completion(prefix, suffix, settings, cache_key=None, regeneration=None):
    settings = normalize_settings(settings)
    prefix = _compress_context_if_needed(prefix, settings, cache_key)
    if settings["provider"] == "ollama":
        raw = _request_ollama_completion(
            prefix,
            suffix,
            settings,
            _completion_token_limit(settings),
            regeneration=regeneration,
        )
    else:
        messages = build_messages(prefix, suffix, settings, regeneration=regeneration)
        raw = _request_openai_messages(messages, settings, _completion_token_limit(settings))
    return clean_completion(raw, prefix, max_words=_prediction_words(settings))


def _request_openai(prefix, suffix, settings):
    return _request_openai_messages(
        build_messages(prefix, suffix, settings),
        settings,
        _completion_token_limit(settings),
    )


def _request_openai_messages(messages, settings, token_limit=None, temperature=None):
    api_key = settings.get("openai_api_key") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OpenAI provider is selected, but no API key is configured.")

    model = settings.get("openai_model")
    if not model:
        raise RuntimeError("OpenAI provider is selected, but no model label is configured.")

    base_url = (settings.get("openai_base_url") or DEFAULT_SETTINGS["openai_base_url"]).rstrip("/")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": _float_setting(settings, "temperature") if temperature is None else temperature,
        "max_completion_tokens": token_limit or _completion_token_limit(settings),
    }
    if not _bool_setting(settings, "allow_reasoning"):
        payload["reasoning_effort"] = "minimal"
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }
    url = base_url + "/chat/completions"
    response = _post_openai_chat_completion(url, payload, headers)
    try:
        return response["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError("OpenAI response did not include a chat completion.")


def _request_ollama(prefix, suffix, settings):
    return _request_ollama_completion(prefix, suffix, settings, _completion_token_limit(settings))


def _request_ollama_completion(
    prefix,
    suffix,
    settings,
    token_limit=None,
    temperature=None,
    regeneration=None,
):
    model = settings.get("ollama_model")
    if not model:
        raise RuntimeError("Ollama provider is selected, but no model label is configured.")

    host = (settings.get("ollama_host") or DEFAULT_SETTINGS["ollama_host"]).rstrip("/")
    request_temperature = _float_setting(settings, "temperature") if temperature is None else temperature
    if regeneration and regeneration.get("attempt"):
        # Raw Ollama generation deliberately has no metaprompt because any
        # instruction text can leak into the prose. Increase sampling diversity
        # slightly on explicit retries while preserving the same output budget.
        request_temperature = min(1.2, request_temperature + min(int(regeneration["attempt"]), 4) * 0.08)
    payload = {
        "model": model,
        "prompt": _ollama_completion_prompt(prefix, suffix, settings),
        "stream": False,
        "raw": True,
        "think": _ollama_think_setting(settings),
        "options": {
            "temperature": request_temperature,
            "num_predict": token_limit or _completion_token_limit(settings),
            "stop": _ollama_completion_stop_sequences(settings),
        },
    }
    response = _post_json(host + "/api/generate", payload, {"Content-Type": "application/json"})
    try:
        return response.get("response", "")
    except Exception:
        raise RuntimeError("Ollama response did not include generated text.")


def _ollama_completion_prompt(prefix, suffix, settings):
    return prefix or ""


def _ollama_completion_stop_sequences(settings):
    # Do not stop at the first paragraph boundary: that made larger prediction
    # targets behave like the old ~24-word default. num_predict and the final
    # word cap now provide the length boundaries.
    stop = []
    if not _bool_setting(settings, "allow_reasoning"):
        stop.extend(
            [
                "<think>",
                "</think>",
                "Okay, let me",
                "Ok, let me",
                "Hmm,",
                "The user",
                "I need to",
                "I should",
                "Possible continuation:",
            ]
        )
    return stop


def _request_ollama_messages(messages, settings, token_limit=None, temperature=None):
    model = settings.get("ollama_model")
    if not model:
        raise RuntimeError("Ollama provider is selected, but no model label is configured.")

    host = (settings.get("ollama_host") or DEFAULT_SETTINGS["ollama_host"]).rstrip("/")
    payload = {
        "model": model,
        "messages": _ollama_messages_for_request(messages, settings),
        "stream": False,
        "think": _ollama_think_setting(settings),
        "options": {
            "temperature": _float_setting(settings, "temperature") if temperature is None else temperature,
            "num_predict": token_limit or _completion_token_limit(settings),
        },
    }
    response = _post_json(host + "/api/chat", payload, {"Content-Type": "application/json"})
    try:
        message = response["message"]
        return message.get("content", "")
    except Exception:
        raise RuntimeError("Ollama response did not include a chat message.")


def _ollama_think_setting(settings):
    return True if _bool_setting(settings, "allow_reasoning") else False


def _ollama_messages_for_request(messages, settings):
    if _bool_setting(settings, "allow_reasoning") or not _is_qwen3_model(settings.get("ollama_model", "")):
        return messages

    updated = []
    added = False
    for message in messages:
        item = dict(message)
        if not added and item.get("role") == "user":
            content = str(item.get("content", "")).rstrip()
            item["content"] = content + "\n\n/no_think"
            added = True
        updated.append(item)

    if not added:
        updated.append({"role": "user", "content": "/no_think"})
    return updated


def _is_qwen3_model(model):
    return "qwen3" in str(model or "").lower()


def _post_openai_chat_completion(url, payload, headers):
    payload = dict(payload)
    removed_reasoning = False
    swapped_token_limit = False
    while True:
        try:
            return _post_json(url, payload, headers)
        except _HttpJsonError as exc:
            if (
                not removed_reasoning
                and "reasoning_effort" in payload
                and _is_unsupported_parameter(exc, "reasoning_effort")
            ):
                payload = dict(payload)
                payload.pop("reasoning_effort", None)
                removed_reasoning = True
                continue
            if (
                not swapped_token_limit
                and "max_completion_tokens" in payload
                and _is_unsupported_parameter(exc, "max_completion_tokens")
            ):
                payload = dict(payload)
                payload["max_tokens"] = payload.pop("max_completion_tokens")
                swapped_token_limit = True
                continue
            raise


def _compress_context_if_needed(prefix, settings, cache_key=None):
    max_words = _int_setting(settings, "max_context_words")
    if max_words <= 0 or _word_count(prefix) <= max_words:
        return prefix

    recent_words = max(SUMMARY_RECENT_MIN_WORDS, int(max_words * SUMMARY_RECENT_RATIO))
    older, recent = _split_recent_words(prefix, recent_words)
    if not older:
        return _last_words(prefix, max_words)

    try:
        summary = _summarize_context(older, settings, cache_key)
    except Exception:
        return _last_words(prefix, max_words)

    if not summary:
        return _last_words(prefix, max_words)

    return (
        "[Compressed earlier context]\n"
        f"{summary.strip()}\n\n"
        "[Recent text before cursor]\n"
        f"{recent.strip()}"
    )


def _summarize_context(text, settings, cache_key=None):
    text = text.strip()
    if not text:
        return ""

    if cache_key is not None:
        cached = _CONTEXT_SUMMARIES.get(cache_key)
        if cached and text.startswith(cached["source"]):
            extra_words = _word_count(text) - cached["source_words"]
            if extra_words <= SUMMARY_REFRESH_WORDS:
                carryover = text[len(cached["source"]) :].strip()
                if carryover:
                    return cached["summary"] + "\nRecent unsummarized context: " + carryover
                return cached["summary"]

    messages = build_summary_messages(text, settings)
    if settings["provider"] == "ollama":
        raw = _request_ollama_messages(messages, settings, _summary_token_limit(settings), temperature=0.2)
    else:
        raw = _request_openai_messages(messages, settings, _summary_token_limit(settings), temperature=0.2)
    summary = clean_completion(raw)

    if cache_key is not None:
        _CONTEXT_SUMMARIES[cache_key] = {
            "source": text,
            "source_words": _word_count(text),
            "summary": summary,
        }
    return summary


def _word_count(text):
    return len(re.findall(r"\S+", text or ""))


def _split_recent_words(text, recent_words):
    matches = list(re.finditer(r"\S+", text or ""))
    if len(matches) <= recent_words:
        return "", text
    split_at = matches[-recent_words].start()
    return text[:split_at].rstrip(), text[split_at:].lstrip()


def _last_words(text, count):
    matches = list(re.finditer(r"\S+", text or ""))
    if len(matches) <= count:
        return text
    return text[matches[-count].start() :].lstrip()


class _HttpJsonError(RuntimeError):
    def __init__(self, code, url, body):
        self.code = code
        self.url = url
        self.body = body
        RuntimeError.__init__(self, f"HTTP {code} from {url}: {body[:1000]}")


def _is_unsupported_parameter(error, parameter):
    if error.code != 400:
        return False
    try:
        body = json.loads(error.body)
        api_error = body.get("error", {})
        return (
            api_error.get("code") in ("unsupported_parameter", "unsupported_value")
            and api_error.get("param") == parameter
        )
    except Exception:
        return (
            "Unsupported parameter" in error.body
            and parameter in error.body
        )


def _post_json(url, payload, headers=None, timeout=90):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise _HttpJsonError(exc.code, url, body)
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
    for getter in (
        lambda: doc.getRuntimeUID(),
        lambda: doc.RuntimeUID,
        lambda: doc.getURL(),
        lambda: doc.URL,
    ):
        try:
            value = getter()
            if value:
                return str(value)
        except Exception:
            pass
    return str(id(doc))


def _get_text_context(doc, settings):
    view_cursor = _view_cursor(doc)
    if not _cursor_is_collapsed(view_cursor):
        raise RuntimeError("Place the cursor where you want autocomplete; selected text is not supported.")

    text = view_cursor.getText()

    # Anchor both ranges at the actual insertion point. In particular, never
    # create the prefix from the document end: text after the cursor must not
    # consume any of the before-cursor character budget.
    prefix_cursor = text.createTextCursorByRange(view_cursor.getStart())
    _go_left(prefix_cursor, _prefix_char_budget(settings), True)
    prefix = prefix_cursor.getString()

    suffix_cursor = text.createTextCursorByRange(view_cursor.getEnd())
    _go_right(suffix_cursor, _int_setting(settings, "suffix_chars"), True)
    suffix = suffix_cursor.getString()

    return view_cursor, prefix, suffix


def _prefix_char_budget(settings):
    legacy_chars = _int_setting(settings, "prefix_chars")
    context_words = _int_setting(settings, "max_context_words")
    word_budget_chars = context_words * 8 if context_words else legacy_chars
    return max(legacy_chars, min(MAX_CONTEXT_CHAR_CAP, word_budget_chars))


def _insert_completion(view_cursor, completion):
    text = view_cursor.getText()
    text.insertString(view_cursor, completion, True)


def _completion_with_context_spacing(prefix, completion):
    if not prefix or not completion:
        return completion

    previous = prefix[-1]
    first = completion[0]
    if previous.isspace() or first.isspace():
        return completion
    if first in NO_SPACE_BEFORE_COMPLETION_START:
        return completion
    if previous in NO_SPACE_AFTER_PREFIX_END:
        return completion
    return " " + completion


def _cursor_is_collapsed(cursor):
    try:
        return cursor.isCollapsed()
    except Exception:
        try:
            return cursor.getString() == ""
        except Exception:
            return True


class GhostCompletion:
    def __init__(
        self,
        doc,
        text_range,
        completion,
        original_properties,
        request_prefix=None,
        request_suffix=None,
        regeneration_attempt=0,
    ):
        self.doc = doc
        self.text_range = text_range
        self.completion = completion
        self.original_properties = original_properties
        self.request_prefix = request_prefix
        self.request_suffix = request_suffix
        self.regeneration_attempt = regeneration_attempt

    def matches_document(self):
        try:
            return self.text_range.getString() == self.completion
        except Exception:
            return False

    def accept(self):
        if self.matches_document():
            _apply_properties(self.text_range, self.original_properties)
            end = self.text_range.getEnd()
            _move_view_cursor_to_range(self.doc, end)
            _restore_insertion_properties(self.doc, end, self.original_properties)

    def accept_prefix(self, count):
        if not self.matches_document():
            return True

        count = max(0, min(int(count), len(self.completion)))
        if count <= 0:
            return False
        if count >= len(self.completion):
            self.accept()
            self.completion = ""
            return True

        text = self.text_range.getText()
        accepted = text.createTextCursorByRange(self.text_range.getStart())
        if not accepted.goRight(count, True):
            return False
        _apply_properties(accepted, self.original_properties)

        remaining = self.completion[count:]
        remaining_range = text.createTextCursorByRange(accepted.getEnd())
        if not remaining_range.goRight(len(remaining), True):
            return False
        _apply_ghost_style(remaining_range)

        self.text_range = remaining_range
        self.completion = remaining
        start = remaining_range.getStart()
        _move_view_cursor_to_range(self.doc, start)
        _restore_insertion_properties(self.doc, start, self.original_properties)
        return False

    def discard(self):
        if not self.matches_document():
            return

        start = self.text_range.getStart()
        self.text_range.setString("")
        _move_view_cursor_to_range(self.doc, start)
        _restore_insertion_properties(self.doc, start, self.original_properties)


def _has_ghost(doc):
    return _doc_key(doc) in _GHOSTS


def _accept_ghost(doc):
    key = _doc_key(doc)
    ghost = _GHOSTS.pop(key, None)
    if ghost:
        ghost.accept()
        _REGENERATION_STATES.pop(key, None)
        return True
    return False


def _accept_partial_ghost(doc, unit):
    key = _doc_key(doc)
    ghost = _GHOSTS.get(key)
    if not ghost:
        return False

    _REGENERATION_STATES.pop(key, None)

    if unit == "word":
        count = _next_ghost_word_count(ghost.completion)
    else:
        count = _next_ghost_char_count(ghost.completion)

    completed = ghost.accept_prefix(count)
    if completed:
        _GHOSTS.pop(key, None)
    return True


def _next_ghost_char_count(text):
    return 1 if text else 0


def _next_ghost_word_count(text):
    if not text:
        return 0

    index = 0
    length = len(text)
    while index < length and text[index].isspace():
        index += 1

    if index >= length:
        return index

    if _is_word_body_char(text[index]):
        while index < length and _is_word_body_char(text[index]):
            index += 1
        return index

    while index < length and not text[index].isspace() and not _is_word_body_char(text[index]):
        index += 1
    return index


def _is_word_body_char(char):
    return char.isalnum() or char in "_'’-"


def _discard_ghost(doc):
    ghost = _GHOSTS.pop(_doc_key(doc), None)
    if ghost:
        ghost.discard()
        return True
    return False


def _reject_ghost(doc):
    key = _doc_key(doc)
    ghost = _GHOSTS.pop(key, None)
    if not ghost:
        return False

    ghost.discard()
    if ghost.request_prefix is None or ghost.request_suffix is None:
        _REGENERATION_STATES.pop(key, None)
        return True

    previous = _REGENERATION_STATES.get(key, {})
    rejected = list(previous.get("rejected", ()))
    rejected.append(ghost.completion)
    _REGENERATION_STATES[key] = {
        "prefix": ghost.request_prefix,
        "suffix": ghost.request_suffix,
        "attempt": ghost.regeneration_attempt + 1,
        "rejected": rejected[-MAX_REJECTED_COMPLETIONS:],
    }
    return True


def _regeneration_for_context(key, prefix, suffix):
    state = _REGENERATION_STATES.get(key)
    if state and state.get("prefix") == prefix and state.get("suffix") == suffix:
        return {
            "attempt": state.get("attempt", 0),
            "rejected": list(state.get("rejected", ())),
        }
    _REGENERATION_STATES.pop(key, None)
    return None


def _show_ghost_completion(
    doc,
    view_cursor,
    completion,
    request_prefix=None,
    request_suffix=None,
    regeneration_attempt=0,
):
    _discard_ghost(doc)

    text = view_cursor.getText()
    original_properties = _capture_properties(view_cursor, GHOST_PROPERTIES)

    text.insertString(view_cursor, completion, True)
    ghost_range = text.createTextCursorByRange(view_cursor.getEnd())
    _go_left(ghost_range, len(completion), True)
    _apply_ghost_style(ghost_range)
    _move_view_cursor_to_range(doc, ghost_range.getStart())

    _GHOSTS[_doc_key(doc)] = GhostCompletion(
        doc,
        ghost_range,
        completion,
        original_properties,
        request_prefix=request_prefix,
        request_suffix=request_suffix,
        regeneration_attempt=regeneration_attempt,
    )


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


def _restore_insertion_properties(doc, text_range, values):
    _apply_properties(text_range, values)
    try:
        _apply_properties(_view_cursor(doc), values)
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


def _go_right(cursor, count, expand):
    remaining = max(0, int(count))
    while remaining:
        step = min(remaining, 32000)
        if not cursor.goRight(step, expand):
            return False
        remaining -= step
        expand = True
    return True


def complete_current_position(doc=None, preview=True, quiet=False):
    doc = doc or _current_document()
    if not _is_writer_document(doc):
        if not quiet:
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
        regeneration = _regeneration_for_context(key, prefix, suffix)
        completion = request_completion(prefix, suffix, settings, key, regeneration=regeneration)
        completion = _completion_with_context_spacing(prefix, completion)
        if completion:
            if preview:
                _show_ghost_completion(
                    doc,
                    view_cursor,
                    completion,
                    request_prefix=prefix,
                    request_suffix=suffix,
                    regeneration_attempt=(regeneration or {}).get("attempt", 0),
                )
            else:
                _insert_completion(view_cursor, completion)
        else:
            if not quiet:
                _message_box(doc, EXTENSION_NAME, "The model did not return a useful continuation.")
    except Exception as exc:
        if not quiet:
            _message_box(doc, EXTENSION_NAME, str(exc))
    finally:
        try:
            status.end()
        except Exception:
            pass
        _BUSY_DOCS.discard(key)


def _maybe_start_continuous_request(doc, event):
    return _schedule_continuous_request_after_idle(doc, event)


def _schedule_continuous_request_after_idle(doc, event=None):
    if _has_ghost(doc):
        return False
    if event is not None and not _is_continuous_typing_event(event):
        return False
    settings = load_settings()
    if not _bool_setting(settings, "continuous_suggestions"):
        return False

    key = _doc_key(doc)
    timer_id = _next_continuous_timer_id()
    timer = threading.Timer(
        CONTINUOUS_IDLE_DELAY_SECONDS,
        _continuous_idle_timer_fired,
        args=(doc, key, timer_id),
    )
    timer.daemon = True

    with _CONTINUOUS_LOCK:
        _cancel_continuous_timer_locked(key)
        _CONTINUOUS_TIMERS[key] = {
            "id": timer_id,
            "timer": timer,
        }

    timer.start()
    return True


def _continuous_idle_timer_fired(doc, key, timer_id):
    with _CONTINUOUS_LOCK:
        current = _CONTINUOUS_TIMERS.get(key)
        if not current or current.get("id") != timer_id:
            return
        _CONTINUOUS_TIMERS.pop(key, None)

    try:
        settings = load_settings()
        if _bool_setting(settings, "continuous_suggestions"):
            _start_continuous_request(doc, settings)
    except Exception:
        pass


def _next_continuous_timer_id():
    global _CONTINUOUS_TIMER_SEQUENCE
    with _CONTINUOUS_LOCK:
        _CONTINUOUS_TIMER_SEQUENCE += 1
        return _CONTINUOUS_TIMER_SEQUENCE


def _cancel_continuous_timer_locked(key):
    current = _CONTINUOUS_TIMERS.pop(key, None)
    if not current:
        return
    try:
        current["timer"].cancel()
    except Exception:
        pass


def _start_continuous_request(doc, settings, force=False):
    key = _doc_key(doc)
    if key in _BUSY_DOCS or _has_ghost(doc):
        return False

    now = time.time()
    with _CONTINUOUS_LOCK:
        if key in _CONTINUOUS_REQUESTS:
            return False
        if not force and now - _LAST_AUTO_REQUEST.get(key, 0) < CONTINUOUS_MIN_INTERVAL_SECONDS:
            return False

    try:
        view_cursor, prefix, suffix = _get_text_context(doc, settings)
    except Exception:
        return False

    if not force and not _has_enough_new_words_for_continuous(key, prefix):
        return False

    request_id = _next_continuous_request_id()
    with _CONTINUOUS_LOCK:
        if key in _CONTINUOUS_REQUESTS:
            return False
        _CONTINUOUS_REQUESTS[key] = {
            "id": request_id,
            "prefix": prefix,
            "started": now,
        }
        _LAST_AUTO_REQUEST[key] = now
        _LAST_AUTO_PREFIX[key] = prefix

    worker = threading.Thread(
        target=_continuous_request_worker,
        args=(doc, key, request_id, prefix, suffix, dict(settings)),
        daemon=True,
    )
    worker.start()
    return True


def _next_continuous_request_id():
    global _CONTINUOUS_SEQUENCE
    with _CONTINUOUS_LOCK:
        _CONTINUOUS_SEQUENCE += 1
        return _CONTINUOUS_SEQUENCE


def _continuous_request_worker(doc, key, request_id, request_prefix, request_suffix, settings):
    try:
        completion = request_completion(request_prefix, request_suffix, settings, key)
    except Exception:
        completion = ""
    _handle_continuous_response(doc, key, request_id, request_prefix, completion, settings)


def _handle_continuous_response(doc, key, request_id, request_prefix, completion, settings):
    with _CONTINUOUS_LOCK:
        current = _CONTINUOUS_REQUESTS.get(key)
        if not current or current.get("id") != request_id:
            return
        _CONTINUOUS_REQUESTS.pop(key, None)

    if not completion or key not in _HANDLERS or _has_ghost(doc):
        return

    try:
        current_settings = load_settings()
        if not _bool_setting(current_settings, "continuous_suggestions"):
            return
        view_cursor, current_prefix, _suffix = _get_text_context(doc, current_settings)
    except Exception:
        return

    state, remaining = _reconcile_continuous_completion(request_prefix, current_prefix, completion)
    if state == "match":
        remaining = _completion_with_context_spacing(current_prefix, remaining)
        if remaining.strip():
            _show_ghost_completion(doc, view_cursor, remaining)
        else:
            _start_continuous_request(doc, current_settings, force=True)
        return

    if state == "mismatch":
        _LAST_AUTO_PREFIX[key] = current_prefix
        _start_continuous_request(doc, current_settings, force=True)


def _reconcile_continuous_completion(request_prefix, current_prefix, completion):
    if not current_prefix.startswith(request_prefix):
        return "mismatch", ""

    typed_tail = current_prefix[len(request_prefix) :]
    remaining = _completion_remainder_after_typed(completion, typed_tail)
    if remaining is None:
        return "mismatch", ""
    return "match", remaining


def _completion_remainder_after_typed(completion, typed_tail):
    if not typed_tail:
        return completion
    if completion.startswith(typed_tail):
        return completion[len(typed_tail) :]

    typed_index = 0
    completion_index = 0
    while typed_index < len(typed_tail) and completion_index < len(completion):
        typed_char = typed_tail[typed_index]
        completion_char = completion[completion_index]
        if typed_char.isspace() and completion_char.isspace():
            while typed_index < len(typed_tail) and typed_tail[typed_index].isspace():
                typed_index += 1
            while completion_index < len(completion) and completion[completion_index].isspace():
                completion_index += 1
            continue
        if typed_char != completion_char:
            return None
        typed_index += 1
        completion_index += 1

    if typed_index == len(typed_tail):
        return completion[completion_index:]
    return None


def _has_enough_new_words_for_continuous(key, current_prefix):
    previous = _LAST_AUTO_PREFIX.get(key, "")
    if not previous:
        return _word_count(current_prefix) >= CONTINUOUS_TRIGGER_WORDS
    if not current_prefix.startswith(previous):
        return _word_count(current_prefix) >= CONTINUOUS_TRIGGER_WORDS
    typed_tail = current_prefix[len(previous) :]
    return _word_count(typed_tail) >= CONTINUOUS_TRIGGER_WORDS


def _reset_continuous_baseline(doc, settings):
    key = _doc_key(doc)
    try:
        _view_cursor, prefix, _suffix = _get_text_context(doc, settings)
    except Exception:
        prefix = ""
    with _CONTINUOUS_LOCK:
        _cancel_continuous_timer_locked(key)
        _LAST_AUTO_PREFIX[key] = prefix
        _LAST_AUTO_REQUEST.pop(key, None)
        _CONTINUOUS_REQUESTS.pop(key, None)


def _clear_continuous_state(key):
    with _CONTINUOUS_LOCK:
        _cancel_continuous_timer_locked(key)
        _LAST_AUTO_PREFIX.pop(key, None)
        _LAST_AUTO_REQUEST.pop(key, None)
        _CONTINUOUS_REQUESTS.pop(key, None)


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
                    _reject_ghost(self.doc)
                    return True
                if key_code == RIGHT and modifiers == 0:
                    _accept_partial_ghost(self.doc, "char")
                    return True
                if key_code == RIGHT and _is_ctrl_only_modifier(modifiers):
                    _accept_partial_ghost(self.doc, "word")
                    return True
                _discard_ghost(self.doc)
                _REGENERATION_STATES.pop(_doc_key(self.doc), None)
                return False

            if key_code == TAB and modifiers == 0:
                complete_current_position(self.doc, preview=True)
                return True

            _schedule_continuous_request_after_idle(self.doc, event)
        except Exception as exc:
            _message_box(self.doc, EXTENSION_NAME, str(exc))
            return True
        return False

    def keyReleased(self, event):
        try:
            _maybe_start_continuous_request(self.doc, event)
        except Exception:
            return False
        return False

    def disposing(self, event):
        self.doc = None


def _is_continuous_boundary(event):
    if getattr(event, "Modifiers", 0) != 0:
        return False
    key_code = getattr(event, "KeyCode", None)
    if key_code in (TAB, ESCAPE):
        return False

    char = str(getattr(event, "KeyChar", "") or "")
    if not char:
        return False
    if char.isspace():
        return True
    return char in ".?!,:;)]}\"'"


def _is_continuous_trigger(event):
    return _is_continuous_boundary(event)


def _is_continuous_typing_event(event):
    if getattr(event, "Modifiers", 0) != 0:
        return False
    key_code = getattr(event, "KeyCode", None)
    if key_code in (TAB, ESCAPE):
        return False

    char = str(getattr(event, "KeyChar", "") or "")
    if not char:
        return False
    return char.isspace() or char.isprintable()


def _is_ctrl_only_modifier(modifiers):
    ctrl_modifiers = MOD1 | MOD3
    return bool(modifiers & ctrl_modifiers) and not bool(modifiers & ~ctrl_modifiers)


def is_autocomplete_enabled(doc=None):
    try:
        doc = doc or _current_document()
    except Exception:
        return False
    return _doc_key(doc) in _HANDLERS


def enable_autocomplete_for_doc(doc, notify=False):
    if not _is_writer_document(doc):
        if notify:
            _message_box(doc, EXTENSION_NAME, "Open a Writer document before enabling autocomplete.")
        return False

    key = _doc_key(doc)
    if key in _HANDLERS:
        return True

    controller = _controller(doc)
    handler = LibreCompleteAIKeyHandler(doc)
    controller.addKeyHandler(handler)
    _HANDLERS[key] = (controller, handler)
    _reset_continuous_baseline(doc, load_settings())
    return True


def disable_autocomplete_for_doc(doc, notify=False):
    key = _doc_key(doc)
    if key not in _HANDLERS:
        return False

    controller, handler = _HANDLERS.pop(key)
    try:
        _discard_ghost(doc)
        _REGENERATION_STATES.pop(key, None)
        _clear_continuous_state(key)
        controller.removeKeyHandler(handler)
    finally:
        pass
    return True


def toggle_autocomplete_for_doc(doc, notify=False):
    if _doc_key(doc) in _HANDLERS:
        disable_autocomplete_for_doc(doc, notify=notify)
        return False
    return enable_autocomplete_for_doc(doc, notify=notify)


def enable_autocomplete(*args):
    doc = _current_document()
    enable_autocomplete_for_doc(doc, notify=True)


def disable_autocomplete(*args):
    doc = _current_document()
    disable_autocomplete_for_doc(doc, notify=True)


def toggle_autocomplete(*args):
    doc = _current_document()
    toggle_autocomplete_for_doc(doc, notify=True)


def complete_now(*args):
    doc = _current_document()
    complete_current_position(doc, preview=is_autocomplete_enabled(doc))


def is_continuous_suggestions_enabled(settings=None):
    return _bool_setting(settings or load_settings(), "continuous_suggestions")


def set_continuous_suggestions_enabled(enabled, doc=None):
    settings = load_settings()
    settings["continuous_suggestions"] = "true" if enabled else "false"
    settings = save_settings(settings)
    if doc is not None:
        if enabled:
            _reset_continuous_baseline(doc, settings)
        else:
            _clear_continuous_state(_doc_key(doc))
    return enabled


def toggle_continuous_suggestions_for_doc(doc=None):
    enabled = not is_continuous_suggestions_enabled()
    return set_continuous_suggestions_enabled(enabled, doc=doc)


def toggle_continuous_suggestions(*args):
    try:
        doc = _current_document()
    except Exception:
        doc = None
    toggle_continuous_suggestions_for_doc(doc)


def show_settings_for_doc(doc=None, ctx=None):
    if doc is None:
        try:
            doc = _current_document()
        except Exception:
            doc = None
    ctx = ctx or _component_context()
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
        _DIALOG_LISTENERS.pop(id(dialog), None)
        dialog.dispose()


def show_settings(*args):
    show_settings_for_doc()


def _create_settings_dialog(ctx, settings):
    smgr = ctx.ServiceManager
    model = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialogModel", ctx)
    model.PositionX = 80
    model.PositionY = 80
    model.Width = 285
    model.Height = 312
    model.Title = "LibreCompleteAI Settings"

    y = 10
    _label(model, "provider_label", 10, y, 70, "Provider")
    provider = _control(model, "com.sun.star.awt.UnoControlListBoxModel", "provider", 88, y - 2, 175, 14)
    provider.Dropdown = True
    provider.StringItemList = ("OpenAI", "Ollama")
    provider.SelectedItems = (0 if settings["provider"] == "openai" else 1,)

    y += 22
    _label(model, "api_key_label", 10, y, 70, "OpenAI key")
    api_key = _edit(model, "openai_api_key", 88, y - 2, 175, settings.get("openai_api_key", ""))
    api_key.EchoChar = 42

    y += 18
    _label(model, "base_url_label", 10, y, 70, "OpenAI base URL")
    _edit(model, "openai_base_url", 88, y - 2, 175, settings.get("openai_base_url", ""))

    y += 18
    _label(model, "openai_model_label", 10, y, 70, "OpenAI model")
    _edit(model, "openai_model", 88, y - 2, 175, settings.get("openai_model", ""))

    y += 22
    _label(model, "ollama_host_label", 10, y, 70, "Ollama host")
    _edit(model, "ollama_host", 88, y - 2, 175, settings.get("ollama_host", ""))

    y += 18
    _label(model, "ollama_model_label", 10, y, 70, "Ollama model")
    _edit(model, "ollama_model", 88, y - 2, 175, settings.get("ollama_model", ""))

    y += 22
    _checkbox(
        model,
        "continuous_suggestions",
        10,
        y - 1,
        235,
        "Continuous autocomplete suggestions",
        _bool_setting(settings, "continuous_suggestions"),
    )

    y += 18
    _checkbox(
        model,
        "allow_reasoning",
        10,
        y - 1,
        235,
        "Allow reasoning",
        _bool_setting(settings, "allow_reasoning"),
    )

    y += 20
    _label(model, "temperature_label", 10, y, 70, "Temperature")
    _edit(model, "temperature", 88, y - 2, 45, settings.get("temperature", ""))

    _label(model, "max_tokens_label", 155, y, 55, "Token cap")
    _edit(model, "max_tokens", 218, y - 2, 45, settings.get("max_tokens", ""))

    y += 18
    _label(model, "context_label", 10, y, 70, "Context words")
    _edit(model, "max_context_words", 88, y - 2, 45, settings.get("max_context_words", ""))
    _slider(model, "max_context_words_slider", 143, y - 1, 120, settings, "max_context_words")

    y += 18
    _label(model, "prediction_label", 10, y, 70, "Prediction words")
    _edit(model, "prediction_words", 88, y - 2, 45, settings.get("prediction_words", ""))
    _slider(model, "prediction_words_slider", 143, y - 1, 120, settings, "prediction_words")

    y += 18
    _label(model, "suffix_label", 10, y, 70, "After chars")
    _edit(model, "suffix_chars", 88, y - 2, 45, settings.get("suffix_chars", ""))

    y += 20
    _label(model, "guidance_label", 10, y, 70, "Writing guidance")
    guidance = _edit(model, "writing_guidance", 88, y - 2, 175, settings.get("writing_guidance", ""))
    guidance.Height = 34
    guidance.MultiLine = True
    guidance.VScroll = True

    _button(model, "ok", 168, 288, 45, "OK", 1)
    _button(model, "cancel", 218, 288, 45, "Cancel", 2)

    dialog = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialog", ctx)
    dialog.setModel(model)
    toolkit = smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", ctx)
    dialog.createPeer(toolkit, None)
    _DIALOG_LISTENERS[id(dialog)] = _connect_slider_listeners(dialog)
    return dialog


def _settings_from_dialog(dialog, current):
    provider_control = dialog.getControl("provider")
    provider = provider_control.getSelectedItem().lower()
    updated = dict(current)
    updated["provider"] = provider
    for name in SETTINGS_FIELDS:
        if name == "provider":
            continue
        if name in BOOLEAN_SETTINGS:
            updated[name] = _get_dialog_bool(dialog, name, current.get(name, "false"))
        else:
            updated[name] = _get_dialog_text(dialog, name, current.get(name, DEFAULT_SETTINGS.get(name, "")))
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


def _checkbox(model, name, x, y, width, text, checked):
    checkbox = _control(model, "com.sun.star.awt.UnoControlCheckBoxModel", name, x, y, width, 12)
    checkbox.Label = text
    checkbox.State = 1 if checked else 0
    return checkbox


def _slider(model, name, x, y, width, settings, setting_name):
    slider = _control(model, "com.sun.star.awt.UnoControlScrollBarModel", name, x, y, width, 10)
    config = SLIDER_SETTINGS[setting_name]
    value = _clamp(_int_setting(settings, setting_name), config["minimum"], config["maximum"])
    for prop, prop_value in (
        ("Orientation", 0),
        ("ScrollValueMin", config["minimum"]),
        ("ScrollValueMax", config["maximum"]),
        ("ScrollValue", value),
        ("LineIncrement", config["step"]),
        ("BlockIncrement", config["step"] * 5),
        ("VisibleSize", config["step"]),
        ("LiveScroll", True),
    ):
        try:
            setattr(slider, prop, prop_value)
        except Exception:
            pass
    return slider


def _button(model, name, x, y, width, text, button_type):
    button = _control(model, "com.sun.star.awt.UnoControlButtonModel", name, x, y, width, 14)
    button.Label = text
    button.PushButtonType = button_type
    return button


def _get_dialog_text(dialog, name, default=""):
    try:
        control = dialog.getControl(name)
    except Exception:
        return default
    return _control_text(control, default)


def _get_dialog_bool(dialog, name, default="false"):
    try:
        control = dialog.getControl(name)
    except Exception:
        return default
    return "true" if _control_state(control, default) else "false"


def _control_text(control, default=""):
    for getter in (
        lambda: control.getText(),
        lambda: control.Text,
        lambda: control.getModel().Text,
        lambda: control.Value,
        lambda: control.getModel().Value,
        lambda: control.getModel().ScrollValue,
    ):
        try:
            return str(getter())
        except Exception:
            pass
    return default


def _set_control_text(control, value):
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


def _control_state(control, default=False):
    if isinstance(default, str):
        default = default.strip().lower() in ("1", "true", "yes", "on", "enabled")
    for getter in (
        lambda: control.getState(),
        lambda: control.State,
        lambda: control.getModel().State,
    ):
        try:
            return int(getter()) != 0
        except Exception:
            pass
    return bool(default)


def _connect_slider_listeners(dialog):
    listeners = []
    for setting_name, config in SLIDER_SETTINGS.items():
        try:
            slider = dialog.getControl(config["slider"])
            target = dialog.getControl(setting_name)
            listener = _SliderToTextListener(target)
            slider.addAdjustmentListener(listener)
            listeners.append(listener)
        except Exception:
            pass
    return listeners


class _SliderToTextListener(unohelper.Base, XAdjustmentListener):
    def __init__(self, target):
        self.target = target

    def adjustmentValueChanged(self, event):
        value = _slider_event_value(event)
        if value is not None and self.target is not None:
            _set_control_text(self.target, str(int(value)))

    def disposing(self, event):
        self.target = None


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


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, int(value)))


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
    toggle_continuous_suggestions,
    complete_now,
)
