# LibreCompleteAI

LibreCompleteAI is a LibreOffice Writer extension that uses an LLM to continue prose at the cursor when you press Tab. It is aimed at drafting, editing, and long-form writing rather than code completion.

It is intended for LibreOffice 7.0 or newer.

## Features

- OpenAI-compatible chat completions with an API key, base URL, and model label.
- Ollama at a local or remote host, using any model you have downloaded.
- A LibreOffice settings dialog for provider and generation options.
- A fallback "Complete Now" menu command if you want to test completion without enabling the Tab hook.
- A ghost-text style preview: press Tab once to preview, press Tab again to accept.

## Build From Source

```powershell
python tools\build_extension.py
```

The installable extension is written to:

```text
dist/LibreCompleteAI.oxt
```

## Install

Build the extension first, or download `LibreCompleteAI.oxt` from the repository's `dist` folder.

In LibreOffice:

```text
Tools > Extension Manager > Add...
```

Choose `dist/LibreCompleteAI.oxt`, install it for the current user, and restart LibreOffice.

You can also install from the command line on Windows:

```powershell
& "C:\Program Files\LibreOffice\program\unopkg.com" add --force --suppress-license dist\LibreCompleteAI.oxt
```

## Configure

In Writer, open:

```text
Tools > Add-ons > LibreCompleteAI Settings...
```

For OpenAI-compatible use, set:

- Provider: `OpenAI`
- API key
- Base URL, usually `https://api.openai.com/v1`
- Model label, for example `gpt-4.1-mini` or another model available to your account

For Ollama use, set:

- Provider: `Ollama`
- Host, usually `http://localhost:11434`
- Model label, matching a local model from `ollama list`

API keys are stored locally in plain text in the user's configuration directory.

## Use

In Writer, choose:

```text
Tools > Add-ons > Enable LibreCompleteAI
```

Then press Tab while the cursor is in document text. The extension sends a small amount of text before and after the cursor to the selected model, asks for a natural continuation, and shows the result as pale temporary text at the cursor.

When a preview is visible:

- Press Tab again to accept it.
- Press Esc to dismiss it.
- Keep typing to dismiss it and continue with your own text.

While enabled, plain Tab is consumed by the extension. Use:

```text
Tools > Add-ons > Disable LibreCompleteAI
```

to restore Writer's normal Tab behavior.

## Notes

LibreOffice Writer does not expose a native Cursor-style ghost text overlay through the simple macro API used here. This extension works around that by inserting tracked, temporary, pale text and deleting or committing it on the next key action.

Because the preview is temporarily real document text, dismiss or accept it before saving. Disabling the extension from the Add-ons menu also removes any active preview. Network calls run synchronously, so Writer may pause briefly while the model responds.
