"""Bring-your-own AI: the user picks a provider and model; "Ask AI" buttons
across the app and the Assistant send a prompt built from what's on screen.

Providers
  anthropic     Anthropic's API via the official SDK (optional dependency).
  openai-style  OpenAI, Google Gemini, OpenRouter, Groq, Ollama, LM Studio or any
                OpenAI-compatible server, via its /chat/completions endpoint.
  chat          No key: copy the prompt and open ChatGPT / Claude / Gemini in the
                browser, so it runs on the user's own chat subscription.

Nothing is sent until the user presses Ask, and the first time per provider
they see exactly what will be sent. Keys live in their own file next to the
settings (owner-only permissions), never in settings.json."""
import json
import os
import threading
import urllib.error
import urllib.request
import webbrowser

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from .storage import data_dir
from .widgets import Card, muted
from .popups import MessageBox, OverlayDialog

SETTINGS_KEY = "ai"
SECRETS_FILE = "ai_keys.json"

# key: (label, kind, default base URL, needs key, default model, help)
PROVIDERS = {
    "off": ("Off", None, "", False, "", "AI features are turned off."),
    "anthropic": ("Anthropic (Claude API)", "anthropic", "", True, "claude-opus-5-5",
                  "Needs an Anthropic API key from console.anthropic.com."),
    "claudecode": ("Claude Code (your Claude subscription, no API key)", "claudecode", "", False, "",
                   "Uses the Claude Code app you're signed in to (Pro or Max plan): no API key, no API "
                   "bill; it counts toward your plan's usage. Install Claude Code (or its VS Code "
                   "extension) and sign in once."),
    "gemini_cli": ("Gemini CLI (your Google account, no API key)", "geminicli", "", False, "",
                   "Uses Google's Gemini CLI signed in with your Google account: free daily allowance, "
                   "more with a Gemini plan. The installer adds it; press Sign in… once."),
    "codex_cli": ("Codex CLI (your ChatGPT account, no API key)", "codexcli", "", False, "",
                  "Uses OpenAI's Codex CLI signed in with your ChatGPT account (Plus, Pro, Team…); counts "
                  "toward your ChatGPT plan. The installer adds it; press Sign in… once."),
    "openai": ("OpenAI (ChatGPT API)", "openai", "https://api.openai.com/v1", True, "",
               "Needs an OpenAI API key from platform.openai.com."),
    "gemini": ("Google Gemini API", "openai", "https://generativelanguage.googleapis.com/v1beta/openai", True, "",
               "Needs a Gemini API key from aistudio.google.com (there's a free tier)."),
    "openrouter": ("OpenRouter (many models, one key)", "openai", "https://openrouter.ai/api/v1", True, "",
                   "Needs an OpenRouter key from openrouter.ai."),
    "ollama": ("Ollama (local, free, private)", "openai", "http://localhost:11434/v1", False, "",
               "Runs on this computer: nothing leaves it. Install Ollama and pull a model first."),
    "lmstudio": ("LM Studio (local, free, private)", "openai", "http://localhost:1234/v1", False, "",
                 "Runs on this computer: start LM Studio's local server first."),
    "custom": ("Other OpenAI-compatible server", "openai", "", False, "",
               "Any server with an OpenAI-style /chat/completions endpoint (Groq, Together, vLLM…)."),
    "chat": ("My AI chat in the browser (no key)", "chat", "", False, "",
             "Copies the question and opens your chat site. Paste it there; it runs on your own subscription."),
}
CHAT_SITES = (("ChatGPT", "https://chatgpt.com/"), ("Claude", "https://claude.ai/new"),
              ("Gemini", "https://gemini.google.com/app"))
ANTHROPIC_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
CLAUDE_CODE_MODELS = ("", "opus", "sonnet", "haiku")  # "" = Claude Code's own default

SYSTEM_BASE = (
    "You are the assistant inside PQ35 Flasher, a desktop app that backs up, flashes, logs and "
    "diagnoses VW PQ35-platform modules (engine ECU, DSG/transmission, Haldex Gen4 AWD, EPS steering "
    "rack, BCM) over CAN. The user is a car enthusiast or tuner. Be practical and specific: say what "
    "the data shows, what is likely normal, what looks wrong, and what to check next. Use the units "
    "given. Where flashing or tuning is involved, put safety first: never encourage flashing without "
    "a backup, on a weak battery, or with an image whose part number doesn't match; say plainly when "
    "something could damage an engine or a module. You only see what the app sends you, so say when "
    "you'd need more data. Keep answers concise and use short headings or bullet points."
)


# --- configuration ---------------------------------------------------------

def config(settings):
    c = dict(settings.get(SETTINGS_KEY) or {})
    c.setdefault("provider", "off")
    c.setdefault("models", {})
    c.setdefault("base_urls", {})
    c.setdefault("chat_site", CHAT_SITES[0][1])
    c.setdefault("approved", [])
    return c


def enabled(settings):
    return config(settings)["provider"] != "off"


class _Hub(QObject):
    """Tells every Ask AI button when AI is switched on or off in Settings."""
    enabled_changed = Signal(bool)


_hub = None


def hub():
    global _hub
    if _hub is None:
        _hub = _Hub()
    return _hub


def provider_label(settings):
    return PROVIDERS[config(settings)["provider"]][0]


def _secrets_path():
    return data_dir() / SECRETS_FILE


def get_key(provider):
    try:
        return json.loads(_secrets_path().read_text(encoding="utf-8")).get(provider, "")
    except (OSError, ValueError):
        return ""


def set_key(provider, key):
    path = _secrets_path()
    try:
        keys = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        keys = {}
    if key:
        keys[provider] = key
    else:
        keys.pop(provider, None)
    path.write_text(json.dumps(keys, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def resolved(settings):
    """(provider, kind, base_url, key, model) for the configured provider."""
    c = config(settings)
    provider = c["provider"]
    _label, kind, base, _needs, default_model, _help = PROVIDERS[provider]
    base = (c["base_urls"].get(provider) or base).rstrip("/")
    model = c["models"].get(provider) or default_model
    return provider, kind, base, get_key(provider), model


# --- calling the model -----------------------------------------------------

class AIError(Exception):
    pass


def _anthropic_stream(key, model, system, messages, effort, on_text, should_stop):
    try:
        import anthropic
    except ImportError:
        raise AIError("The Anthropic provider needs the 'anthropic' package. Install it with: "
                      "uv sync --extra ai  (or pip install anthropic).") from None
    client = anthropic.Anthropic(api_key=key or None, max_retries=2)
    try:
        # Opus 5.5: adaptive thinking is always on (omit `thinking`); effort sets depth.
        # Server-side fallback lets the API rescue a declined request on another model.
        with client.beta.messages.stream(
                model=model, max_tokens=32000, system=system, messages=messages,
                output_config={"effort": effort},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default") as stream:
            for text in stream.text_stream:
                if should_stop():
                    break
                on_text(text)
            final = None if should_stop() else stream.get_final_message()
    except TypeError as e:
        if "authentication" in str(e).lower():
            raise AIError("No Anthropic API key yet. Paste one in Settings → AI and press Save key.") from None
        raise
    except anthropic.AuthenticationError:
        raise AIError("Anthropic rejected the API key. Check it in Settings → AI.") from None
    except anthropic.PermissionDeniedError:
        raise AIError("This API key can't use that model.") from None
    except anthropic.NotFoundError:
        raise AIError(f"Model “{model}” wasn't found. Pick another in Settings → AI.") from None
    except anthropic.RateLimitError:
        raise AIError("Rate limited by Anthropic. Wait a minute and try again.") from None
    except anthropic.APIStatusError as e:
        raise AIError(f"Anthropic error {e.status_code}: {e.message}") from None
    except anthropic.APIConnectionError:
        raise AIError("Couldn't reach Anthropic. Check the internet connection.") from None
    if final is not None and final.stop_reason == "refusal":
        on_text("\n\n_The model declined to answer this request._")
    elif final is not None and final.stop_reason == "max_tokens":
        on_text("\n\n_(Answer cut off: it hit the length limit.)_")


def _openai_stream(base, key, model, system, messages, on_text, should_stop):
    if not base:
        raise AIError("Set the server address in Settings → AI.")
    if not model:
        raise AIError("Choose a model in Settings → AI (Fetch models lists what the server has).")
    body = {"model": model, "stream": True,
            "messages": [{"role": "system", "content": system}] + messages}
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw in resp:
                if should_stop():
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                for choice in chunk.get("choices") or []:
                    text = (choice.get("delta") or {}).get("content")
                    if text:
                        on_text(text)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 403):
            raise AIError(f"The server rejected the API key ({e.code}). Check it in Settings → AI.") from None
        if e.code == 404:
            raise AIError(f"Not found (404): check the model name and server address. {detail}") from None
        raise AIError(f"Server error {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise AIError(f"Couldn't reach {base}: {e.reason}. Is the server running / are you online?") from None


def _install_base():
    """Where install.sh / install.ps1 put the AI tools and their private Node.js."""
    import sys
    from pathlib import Path
    home = Path.home()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")) / "PQ35-Flasher-UI"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "PQ35-Flasher-UI"
    return Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / "pq35-flasher-ui"


def _tool_dirs():
    import sys
    base = _install_base()
    if sys.platform == "win32":
        return [base / "ai-tools"], [base / "node"]
    return [base / "ai-tools" / "bin"], [base / "node" / "bin"]


def tool_env():
    """Environment for running the AI tools: their private Node.js on PATH."""
    tools, nodes = _tool_dirs()
    env = dict(os.environ)
    extra = [str(d) for d in nodes + tools if d.is_dir()]
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


CLI_TOOLS = {  # provider -> (command name, settings key for a manual path, sign-in command)
    "claudecode": ("claude", "claude_path", []),
    "gemini_cli": ("gemini", "gemini_path", []),
    "codex_cli": ("codex", "codex_path", ["login"]),
}


def find_tool(name, settings=None, override_key=None):
    """A CLI tool: Settings override, the installer's folder, then PATH."""
    import shutil
    import sys
    from pathlib import Path
    override = (config(settings).get(override_key) or "").strip() if settings and override_key else ""
    if override:
        return override if Path(override).is_file() else None
    tools, _nodes = _tool_dirs()
    names = [name + ext for ext in ((".cmd", ".exe", ".ps1") if sys.platform == "win32" else ("",))]
    for d in tools:
        for n in names:
            if (d / n).is_file():
                return str(d / n)
    return shutil.which(name, path=tool_env()["PATH"])


def find_claude_code(settings=None):
    """Path to a Claude Code executable: Settings override, the installer's
    folder, PATH, standalone install, or the VS Code / Cursor extension's copy."""
    import glob
    import sys
    from pathlib import Path
    found = find_tool("claude", settings, "claude_path")
    if found or (config(settings).get("claude_path") if settings else None):
        return found
    exe = "claude.exe" if sys.platform == "win32" else "claude"
    home = Path.home()
    candidates = [home / ".claude" / "local" / exe, home / ".local" / "bin" / exe,
                  Path("/opt/homebrew/bin") / exe, Path("/usr/local/bin") / exe]
    for editor in (".vscode", ".vscode-insiders", ".cursor", ".windsurf"):
        pattern = str(home / editor / "extensions" / "anthropic.claude-code-*" / "resources" / "native-binary" / exe)
        candidates += [Path(p) for p in sorted(glob.glob(pattern), reverse=True)]  # newest version first
    return next((str(c) for c in candidates if c.is_file()), None)


def _claude_code_stream(settings, model, system, messages, on_text, should_stop, set_process):
    import subprocess
    import sys
    import tempfile
    exe = find_claude_code(settings)
    if not exe:
        raise AIError("Claude Code wasn't found. Install it (claude.com/claude-code) or its VS Code "
                      "extension and sign in, or set its location in Settings → AI.")
    # One-shot run: earlier turns go in as a transcript.
    if len(messages) == 1:
        prompt = messages[0]["content"]
    else:
        parts = [("User: " if m["role"] == "user" else "Assistant: ") + m["content"] for m in messages[:-1]]
        prompt = ("Conversation so far:\n\n" + "\n\n".join(parts) +
                  "\n\nNow answer the user's next message:\n" + messages[-1]["content"])
    args = [exe, "-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
            "--system-prompt", system, "--output-format", "stream-json",
            "--include-partial-messages", "--verbose"]
    if model:
        args += ["--model", model]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with tempfile.TemporaryDirectory(prefix="pq35-ai-") as cwd:  # empty folder: nothing to read
        try:
            proc = subprocess.Popen(args, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                    errors="replace", creationflags=flags, env=tool_env())
        except OSError as e:
            raise AIError(f"Couldn't start Claude Code ({exe}): {e}") from None
        set_process(proc)
        proc.stdin.write(prompt)
        proc.stdin.close()
        result = None
        streamed = False
        for line in proc.stdout:
            if should_stop():
                proc.kill()
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "stream_event":
                inner = event.get("event") or {}
                delta = inner.get("delta") or {}
                if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    streamed = True
                    on_text(delta.get("text", ""))
            elif event.get("type") == "result":
                result = event
        proc.wait()
        if should_stop():
            return
        stderr = proc.stderr.read().strip()
        if result is None:
            hint = " Open Claude Code once and sign in." if "log" in stderr.lower() or "auth" in stderr.lower() else ""
            raise AIError(f"Claude Code stopped without an answer.{hint} {stderr[:300]}".strip())
        if result.get("is_error"):
            text = str(result.get("result") or stderr or "unknown error")
            if "login" in text.lower() or "auth" in text.lower():
                text += " (open Claude Code and sign in with your Claude account)"
            raise AIError(f"Claude Code: {text[:400]}")
        if not streamed and result.get("result"):
            on_text(result["result"])


def _transcript_prompt(system, messages):
    """One-shot CLIs get the instructions and earlier turns as plain text."""
    parts = [f"Instructions for you:\n{system}"]
    if len(messages) > 1:
        parts.append("Conversation so far:\n\n" + "\n\n".join(
            ("User: " if m["role"] == "user" else "Assistant: ") + m["content"] for m in messages[:-1]))
        parts.append("Now answer the user's next message:\n" + messages[-1]["content"])
    else:
        parts.append(messages[-1]["content"])
    return "\n\n".join(parts)


def _run_cli(label, args, prompt, on_text, should_stop, set_process, cwd, stream_stdout=True):
    """Run a one-shot CLI with the prompt on stdin. Returns (stdout_text, stderr_text, code)."""
    import subprocess
    import sys
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(args, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                creationflags=flags, env=tool_env())
    except OSError as e:
        raise AIError(f"Couldn't start {label} ({args[0]}): {e}") from None
    set_process(proc)
    # stderr on a thread so a chatty tool can't fill its pipe and stall
    err = []
    t = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)
    t.start()
    proc.stdin.write(prompt)
    proc.stdin.close()
    out = []
    for line in proc.stdout:
        if should_stop():
            proc.kill()
            break
        out.append(line)
        if stream_stdout:
            on_text(line)
    proc.wait()
    t.join(timeout=5)
    return "".join(out), "".join(err), proc.returncode


def _signin_hint(text):
    low = text.lower()
    return any(w in low for w in ("log in", "login", "sign in", "auth", "credential", "token", "unauthor"))


def _gemini_stream(settings, model, system, messages, on_text, should_stop, set_process):
    import tempfile
    exe = find_tool("gemini", settings, "gemini_path")
    if not exe:
        raise AIError("Gemini CLI wasn't found. Re-run the PQ35 Flasher installer (it adds the AI tools) "
                      "or set its location in Settings → AI.")
    # -p "" = headless; the prompt itself comes on stdin. Plan mode = read-only, no tool actions.
    args = [exe, "-p", "", "--approval-mode", "plan", "-o", "text"]
    if model:
        args += ["-m", model]
    with tempfile.TemporaryDirectory(prefix="pq35-ai-") as cwd:
        out, err, code = _run_cli("Gemini CLI", args, _transcript_prompt(system, messages),
                                  on_text, should_stop, set_process, cwd)
    if should_stop():
        return
    if code != 0 or not out.strip():
        detail = (err or out).strip()[-400:]
        hint = " Press Sign in… in Settings → AI and choose Login with Google." if _signin_hint(detail) else ""
        raise AIError(f"Gemini CLI didn't answer.{hint} {detail}".strip())


def _codex_stream(settings, model, system, messages, on_text, should_stop, set_process):
    import tempfile
    from pathlib import Path
    exe = find_tool("codex", settings, "codex_path")
    if not exe:
        raise AIError("Codex CLI wasn't found. Re-run the PQ35 Flasher installer (it adds the AI tools) "
                      "or set its location in Settings → AI.")
    with tempfile.TemporaryDirectory(prefix="pq35-ai-") as cwd:
        answer = Path(cwd) / "answer.txt"
        # Read-only sandbox in an empty folder, nothing saved: it can only answer.
        args = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
                "--ignore-rules", "-C", cwd, "--json", "-o", str(answer)]
        if model:
            args += ["-m", model]
        args.append("-")
        out, err, code = _run_cli("Codex CLI", args, _transcript_prompt(system, messages),
                                  on_text, should_stop, set_process, cwd, stream_stdout=False)
        if should_stop():
            return
        errors = []
        for line in out.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "error" and event.get("message"):
                errors.append(event["message"])
        text = answer.read_text(encoding="utf-8", errors="replace").strip() if answer.is_file() else ""
    if text:
        on_text(text)
        return
    detail = (errors[-1] if errors else (err.strip()[-400:] or f"exit code {code}"))
    hint = " Press Sign in… in Settings → AI and choose Sign in with ChatGPT." if _signin_hint(detail) else ""
    raise AIError(f"Codex CLI didn't answer.{hint} {detail}".strip())


def open_signin(provider):
    """Open a terminal window running the tool's sign-in, so the user can log in."""
    import shlex
    import subprocess
    import sys
    import tempfile
    name, key, signin = CLI_TOOLS[provider]
    exe = find_claude_code() if provider == "claudecode" else find_tool(name)
    if not exe:
        raise AIError(f"{name} isn't installed. Re-run the PQ35 Flasher installer first.")
    env_path = tool_env()["PATH"]
    if sys.platform == "win32":
        cmd = f'set "PATH={env_path}" && "{exe}" {" ".join(signin)}'
        subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", cmd])
        return
    line = f"export PATH={shlex.quote(env_path)}; cd ~; echo 'Sign in, then close this window.'; " \
           f"{shlex.quote(exe)} {' '.join(signin)}"
    if sys.platform == "darwin":
        script = tempfile.NamedTemporaryFile("w", suffix=".command", delete=False)
        script.write("#!/bin/sh\n" + line + "\n")
        script.close()
        os.chmod(script.name, 0o700)
        subprocess.Popen(["open", script.name])
        return
    for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["konsole", "-e"], ["xterm", "-e"]):
        try:
            subprocess.Popen(term + ["sh", "-c", line + "; exec sh"])
            return
        except OSError:
            continue
    raise AIError(f"Couldn't open a terminal. Run this yourself to sign in:\n{exe} {' '.join(signin)}")


def list_models(settings):
    provider, kind, base, key, _model = resolved(settings)
    if kind == "anthropic":
        try:
            import anthropic
            return [m.id for m in anthropic.Anthropic(api_key=key or None).models.list()]
        except ImportError:
            return list(ANTHROPIC_MODELS)
        except Exception as e:
            raise AIError(f"Couldn't list models: {e}") from None
    if kind == "claudecode":
        return [m for m in CLAUDE_CODE_MODELS if m]
    if kind in ("geminicli", "codexcli"):
        return []
    if kind == "openai":
        req = urllib.request.Request(f"{base}/models", headers={"Authorization": f"Bearer {key}"} if key else {})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            raise AIError(f"Couldn't list models from {base}: {e}") from None
        return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))
    return []


class Ask(QObject):
    """One streamed request on a background thread. Signals arrive on the UI thread."""
    text = Signal(str)
    done = Signal()
    failed = Signal(str)

    def __init__(self, settings, system, messages, effort="medium", parent=None):
        super().__init__(parent)
        self.settings, self.system, self.messages, self.effort = settings, system, messages, effort
        self._stop = False
        self._process = None

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop = True
        if self._process is not None and self._process.poll() is None:
            self._process.kill()

    def _run(self):
        provider, kind, base, key, model = resolved(self.settings)
        try:
            if kind == "anthropic":
                _anthropic_stream(key, model, self.system, self.messages, self.effort,
                                  self.text.emit, lambda: self._stop)
            elif kind == "openai":
                _openai_stream(base, key, model, self.system, self.messages, self.text.emit, lambda: self._stop)
            elif kind == "claudecode":
                _claude_code_stream(self.settings, model, self.system, self.messages, self.text.emit,
                                    lambda: self._stop, lambda proc: setattr(self, "_process", proc))
            elif kind == "geminicli":
                _gemini_stream(self.settings, model, self.system, self.messages, self.text.emit,
                               lambda: self._stop, lambda proc: setattr(self, "_process", proc))
            elif kind == "codexcli":
                _codex_stream(self.settings, model, self.system, self.messages, self.text.emit,
                              lambda: self._stop, lambda proc: setattr(self, "_process", proc))
            else:
                raise AIError("No AI provider is set up. Choose one in Settings → AI.")
            self.done.emit()
        except AIError as e:
            self.failed.emit(str(e))
        except Exception as e:  # keep the UI alive whatever the network does
            self.failed.emit(f"{type(e).__name__}: {e}")


# --- UI --------------------------------------------------------------------

def _chat_handoff(parent, settings, system, prompt):
    text = f"{system}\n\n---\n\n{prompt}"
    QGuiApplication.clipboard().setText(text)
    url = config(settings)["chat_site"]
    webbrowser.open(url)
    MessageBox.information(parent, "Sent to your AI chat",
                            "The question and data are on the clipboard and your chat site is opening.\n\n"
                            "Paste (Ctrl/⌘+V) into a new chat and send it.")


def confirm_send(parent, settings, preview):
    """First time per provider: show exactly what will be sent."""
    c = config(settings)
    provider = c["provider"]
    if provider in c["approved"] or provider in ("ollama", "lmstudio"):
        return True
    dlg = OverlayDialog(parent)
    dlg.setWindowTitle("Send to AI?")
    dlg.CARD_ICON = "send"
    dlg.setMinimumSize(640, 480)
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel(f"This is what will be sent to <b>{PROVIDERS[provider][0]}</b>. "
                         "It leaves this computer. No files are uploaded, only this text."))
    box = QPlainTextEdit(preview)
    box.setReadOnly(True)
    lay.addWidget(box, 1)
    remember = QCheckBox("Don't ask again for this provider")
    lay.addWidget(remember)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Send")
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    lay.addWidget(buttons)
    if not dlg.exec():
        return False
    if remember.isChecked():
        c["approved"] = sorted(set(c["approved"]) | {provider})
        settings.set(SETTINGS_KEY, c)
    return True


class AskDialog(QDialog):
    """Ask AI about something: editable question, streamed answer, follow-ups."""

    def __init__(self, settings, title, context, question, extra_system="", effort="high", parent=None,
                 live_system=None):
        super().__init__(parent)
        self.settings = settings
        self.context = context
        self.extra_system = extra_system
        self.live_system = live_system  # callable -> text, refreshed on every question
        self.system = SYSTEM_BASE + ("\n\n" + extra_system if extra_system else "")
        self.effort = effort
        self.messages = []
        self.answer = ""
        self.request = None
        self.setWindowTitle(f"Ask AI: {title}")
        self.setMinimumSize(760, 620)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        lay = QVBoxLayout(self)
        lay.addWidget(muted(f"Using {provider_label(settings)}. Change it in Settings → AI. "
                            "AI can be wrong, so double-check anything before you flash."))
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        lay.addWidget(self.view, 1)
        self.status = muted("")
        lay.addWidget(self.status)
        # Suggested questions: one click asks it (set_suggestions). Typing still works below.
        from .widgets import FlowLayout
        self.suggest_box = QWidget()
        self.suggest_row = FlowLayout(self.suggest_box)
        self.suggest_box.hide()
        lay.addWidget(self.suggest_box)
        self.refresh_hook = None  # called when the window is activated and after each answer
        row = QHBoxLayout()
        self.input = QPlainTextEdit(question)
        self.input.setMaximumHeight(90)
        self.input.installEventFilter(self)  # Return sends, Shift+Return = new line
        row.addWidget(self.input, 1)
        col = QVBoxLayout()
        self.send = QPushButton("Ask")
        self.send.setObjectName("primary")
        self.send.clicked.connect(self._send)
        col.addWidget(self.send)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        col.addWidget(self.stop_button)
        row.addLayout(col)
        lay.addLayout(row)
        bottom = QHBoxLayout()
        self.show_data = QPushButton("Show what's sent")
        self.show_data.clicked.connect(lambda: MessageBox.information(
            self, "Data sent with the question",
            (self.context or (self.live_system() if self.live_system else ""))[:6000] or "(none)"))
        bottom.addWidget(self.show_data)
        copy = QPushButton("Copy answer")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._transcript()))
        bottom.addWidget(copy)
        bottom.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        bottom.addWidget(close)
        lay.addLayout(bottom)
        self._render()

    def set_suggestions(self, questions):
        """Clickable suggestions above the input (empty list hides the row). Each is a question,
        or (label, request): the chip and the chat show the short label, the AI gets the request."""
        while self.suggest_row.count():
            item = self.suggest_row.takeAt(0)
            if item.widget():
                item.widget().hide()  # gone now, not only when Qt gets round to deleting it
                item.widget().deleteLater()
        from . import theme
        c = theme.colors()
        for q in questions:
            label, request = (q, q) if isinstance(q, str) else q
            b = QPushButton(label)
            if request != label:
                b.setToolTip(request)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"QPushButton {{ border-radius: 14px; padding: 4px 12px; min-height: 0; "
                            f"font-weight: 500; background: {c['surface2']}; color: {c['text']}; "
                            f"border: 1px solid {c['border']}; }}"
                            f"QPushButton:hover {{ border-color: {c['accent']}; background: {c['accent_bg']}; }}"
                            f"QPushButton:disabled {{ color: {c['muted']}; }}")
            b.clicked.connect(lambda _=False, l=label, r=request: self._ask_suggestion(l, r))
            b.setEnabled(self.send.isEnabled())
            self.suggest_row.addWidget(b)
        self.suggest_box.setVisible(bool(questions))
        if not self.messages:
            self._render()

    def _ask_suggestion(self, label, request):
        self.input.setPlainText(request)
        self._send(display=label)

    def _refresh(self):
        if self.refresh_hook is not None:
            try:
                self.refresh_hook(self)
            except Exception:
                pass

    def changeEvent(self, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._refresh()
        super().changeEvent(event)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if (obj is self.input and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            if self.send.isEnabled():
                self._send()
            return True
        return super().eventFilter(obj, event)

    def _transcript(self):
        parts = []
        for m in self.messages:
            text = m.get("display") or m["content"]
            if m["role"] == "user" and m is self.messages[0]:
                text = text.split("\n\n## Data\n", 1)[0]
            parts.append(("**You:** " if m["role"] == "user" else "") + text)
        if self.answer and (not self.messages or self.messages[-1]["role"] == "user"):
            parts.append(self.answer)
        return "\n\n---\n\n".join(parts)

    def _render(self):
        empty = ("_Edit the question below if you like, then press **Ask**._" if self.input.toPlainText().strip()
                 else "_Pick a suggestion below, or type your own question._")
        md = self._transcript() or empty
        self.view.setMarkdown(md)
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def _send(self, display=None):
        question = self.input.toPlainText().strip()
        if not question:
            return
        kind = resolved(self.settings)[1]
        if self.live_system is not None:
            self.system = SYSTEM_BASE + "\n\n" + self.extra_system + "\n\n" + self.live_system()
        first = not self.messages
        content = f"{question}\n\n## Data\n{self.context}" if first and self.context else question
        if kind == "chat":
            _chat_handoff(self, self.settings, self.system, content)
            return
        if kind is None:
            MessageBox.information(self, "AI is off", "Choose an AI provider in Settings → AI first.")
            return
        if first and not confirm_send(self, self.settings, f"[instructions]\n{self.system}\n\n[message]\n{content}"):
            return
        message = {"role": "user", "content": content}
        if display and display != question:
            message["display"] = display  # what the chat shows (a suggestion's short label)
        self.messages.append(message)
        self.answer = ""
        self.input.clear()
        self._busy(True)
        self.request = Ask(self.settings, self.system,
                           [{"role": m["role"], "content": m["content"]} for m in self.messages], self.effort, self)
        self.request.text.connect(self._chunk)
        self.request.done.connect(self._finished)
        self.request.failed.connect(self._failed)
        self.request.start()
        self._render()

    def _chunk(self, text):
        self.answer += text
        self._render()

    def _finished(self):
        self.messages.append({"role": "assistant", "content": self.answer or "(no answer)"})
        self.answer = ""
        self._busy(False)
        self.status.setText("Ask a follow-up question below.")
        self._render()
        self._refresh()

    def _failed(self, message):
        if self.messages and self.messages[-1]["role"] == "user":
            last = self.messages.pop()
            if not self.input.toPlainText().strip():
                self.input.setPlainText(last["content"].split("\n\n## Data\n", 1)[0])
        self._busy(False)
        self.status.setText("")
        MessageBox.warning(self, "AI", message)
        self._render()

    def _stop(self):
        if self.request:
            self.request.stop()

    def _busy(self, on):
        self.send.setEnabled(not on)
        for i in range(self.suggest_row.count()):
            w = self.suggest_row.itemAt(i).widget()
            if w:
                w.setEnabled(not on)
        self.stop_button.setEnabled(on)
        self.status.setText("Thinking…" if on else "")

    def closeEvent(self, event):
        self._stop()
        super().closeEvent(event)


def ask(parent, settings, title, context, question, extra_system="", effort="high"):
    """Ask AI about one thing in its own window (right-click on a fault code)."""
    if not enabled(settings):
        if MessageBox.question(parent, "AI is off",
                                "Ask AI uses an AI service you choose: your own API key, a local model, "
                                "or your chat site in the browser.\n\nOpen Settings to choose one now?") \
                == MessageBox.StandardButton.Yes:
            from PySide6.QtWidgets import QApplication
            for w in QApplication.topLevelWidgets():
                if hasattr(w, "_switch_tab"):
                    w._switch_tab("settings")
                    page = getattr(w, "pages", {}).get("settings")
                    if hasattr(page, "show_section"):
                        page.show_section("AI")
        return
    dlg = AskDialog(settings, title, context, question, extra_system, effort, parent)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dlg.show()
    return dlg


class AISettingsCard(Card):
    def __init__(self, settings):
        super().__init__("AI")
        self.settings = settings
        self.hint("Use your own AI for feedback on logs, maps, fault codes and flash plans, and for "
                  "the Assistant.")
        form = QFormLayout()
        self.form = form
        self.provider = QComboBox()
        for key, (label, *_rest) in PROVIDERS.items():
            self.provider.addItem(label, key)
        form.addRow("Provider", self.provider)
        self.help = muted("")
        self.help.setWordWrap(True)
        self.base = QLineEdit()
        self.base.setPlaceholderText("https://…/v1")
        form.addRow("Server address", self.base)
        key_row = QHBoxLayout()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Paste your API key")
        key_row.addWidget(self.key, 1)
        self.key_save = QPushButton("Save key")
        self.key_save.clicked.connect(self._save_key)
        key_row.addWidget(self.key_save)
        self.key_row_label = QLabel("API key")
        form.addRow(self.key_row_label, key_row)
        self.key_row = key_row
        model_row = QHBoxLayout()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setMinimumWidth(280)
        model_row.addWidget(self.model, 1)
        self.fetch = QPushButton("Fetch models")
        self.fetch.clicked.connect(self._fetch)
        model_row.addWidget(self.fetch)
        self.model_label = QLabel("Model")
        form.addRow(self.model_label, model_row)
        self.model_row = model_row
        claude_row = QHBoxLayout()
        self.claude_path = QLineEdit()
        claude_row.addWidget(self.claude_path, 1)
        self.claude_browse = QPushButton("Choose…")
        self.claude_browse.clicked.connect(self._choose_claude)
        claude_row.addWidget(self.claude_browse)
        self.signin = QPushButton("Sign in…")
        self.signin.setToolTip("Opens a terminal window with the tool's sign-in. Do it once.")
        self.signin.clicked.connect(self._signin)
        claude_row.addWidget(self.signin)
        self.claude_label = QLabel("Claude Code")
        form.addRow(self.claude_label, claude_row)
        self.claude_row = claude_row
        self.site = QComboBox()
        for label, url in CHAT_SITES:
            self.site.addItem(label, url)
        self.site_label = QLabel("Chat site")
        form.addRow(self.site_label, self.site)
        self.add(form)
        self.test_row = QWidget()  # hidden as a whole when the provider has nothing to test
        row = QHBoxLayout(self.test_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.test = QPushButton("Test")
        self.test.clicked.connect(self._test)
        row.addWidget(self.test)
        self.result = muted("")
        self.result.setWordWrap(True)
        row.addWidget(self.result, 1)
        form.addRow("", self.test_row)
        privacy = muted("Off by default. Nothing is sent until you press Ask. Keys are stored only on this "
                        "computer, in their own file (not in settings.json). Local models (Ollama, LM Studio) "
                        "keep everything on this computer.")
        privacy.setWordWrap(True)
        # Wrapped notes go in the card body, indented to the control column: inside the form,
        # Qt sizes wrapped text for a much narrower width and leaves a tall gap.
        self.help_row = self.add(self._indented(self.help))
        self.add(self._indented(privacy))
        self.provider.setToolTip("Which AI to use: an API key, a sign-in app you already have "
                                 "(Claude Code, Gemini CLI, Codex CLI), a local model, or your chat site.")
        self.base.setToolTip("The server's address, ending in /v1 for most OpenAI-compatible servers.")
        self.key.setToolTip("Your API key from the provider's developer site. Press Save key to keep it.")
        self.model.setToolTip("Which model to use. Fetch models lists what your key can use.")
        self.claude_path.setToolTip("Where the tool's program is. Leave empty to find it automatically.")
        self.site.setToolTip("The chat site that opens when you ask (paste the copied question there).")
        self._load()
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.base.editingFinished.connect(self._save)
        self.claude_path.editingFinished.connect(self._save)
        self.model.currentTextChanged.connect(lambda _t: self._save())
        self.site.currentIndexChanged.connect(lambda _i: self._save())

    NOTE_INDENT = 126  # the Settings page's label column (110) + its spacing (16)

    def _indented(self, label):
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(self.NOTE_INDENT, 0, 0, 0)
        row.addWidget(label, 1)
        return holder

    def _load(self):
        c = config(self.settings)
        self.provider.blockSignals(True)
        self.provider.setCurrentIndex(max(self.provider.findData(c["provider"]), 0))
        self.provider.blockSignals(False)
        self.site.blockSignals(True)
        self.site.setCurrentIndex(max(self.site.findData(c["chat_site"]), 0))
        self.site.blockSignals(False)
        self._show_provider()

    def _show_provider(self):
        c = config(self.settings)
        p = self.provider.currentData()
        _label, kind, base, needs_key, default_model, help_text = PROVIDERS[p]
        self.help.setText(help_text)
        self.help_row.setVisible(bool(help_text))
        self.form.setRowVisible(self.base, kind == "openai")
        self.base.blockSignals(True)
        self.base.setText(c["base_urls"].get(p) or base)
        self.base.blockSignals(False)
        show_key = kind in ("anthropic", "openai")
        self.form.setRowVisible(self.key_row, show_key)
        self.key.clear()
        self.key.setPlaceholderText("Saved ✓ (paste a new one to replace)" if get_key(p) else
                                    ("Paste your API key" if needs_key else "Optional"))
        cli = kind in ("claudecode", "geminicli", "codexcli")
        self.form.setRowVisible(self.model_row, kind in ("anthropic", "openai") or cli)
        self.fetch.setVisible(kind in ("anthropic", "openai"))
        self.model.lineEdit().setPlaceholderText("The tool's default model" if cli else "")
        self.form.setRowVisible(self.claude_row, cli)
        if cli:
            name, key, _signin = CLI_TOOLS[p]
            found = find_claude_code() if p == "claudecode" else find_tool(name)
            self.claude_label.setText({"claudecode": "Claude Code", "gemini_cli": "Gemini CLI",
                                       "codex_cli": "Codex CLI"}[p])
            self.claude_path.setPlaceholderText(f"Found: {found}" if found else
                                                "Not found: re-run the installer (it adds the AI tools)")
            self.claude_path.setText(c.get(key, ""))
        self.model.blockSignals(True)
        self.model.clear()
        if kind == "anthropic":
            self.model.addItems(list(ANTHROPIC_MODELS))
        elif kind == "claudecode":
            self.model.addItems(list(CLAUDE_CODE_MODELS))
        self.model.setEditText(c["models"].get(p) or default_model)
        self.model.blockSignals(False)
        self.form.setRowVisible(self.site, kind == "chat")
        self.form.setRowVisible(self.test_row, kind in ("anthropic", "openai") or cli)
        self.result.setText("")

    def _provider_changed(self, _i):
        self._show_provider()
        self._save()

    def _save(self):
        c = config(self.settings)
        p = self.provider.currentData()
        c["provider"] = p
        if self.base.isVisible() and self.base.text().strip():
            c["base_urls"][p] = self.base.text().strip()
        if self.model.isVisible():
            c["models"][p] = self.model.currentText().strip()
        c["chat_site"] = self.site.currentData()
        if self.claude_path.isVisible() and p in CLI_TOOLS:
            c[CLI_TOOLS[p][1]] = self.claude_path.text().strip()
        self.settings.set(SETTINGS_KEY, c)
        hub().enabled_changed.emit(p != "off")

    def _signin(self):
        try:
            open_signin(self.provider.currentData())
            self.result.setText("A terminal window opened: sign in there, close it, then press Test.")
        except AIError as e:
            self.result.setText(str(e))

    def _choose_claude(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Find Claude Code", "", "All files (*)")
        if path:
            self.claude_path.setText(path)
            self._save()
            self._show_provider()

    def _save_key(self):
        set_key(self.provider.currentData(), self.key.text().strip())
        self._show_provider()
        self.result.setText("Key saved.")

    def _fetch(self):
        self._save()
        try:
            models = list_models(self.settings)
        except AIError as e:
            self.result.setText(str(e))
            return
        current = self.model.currentText()
        self.model.clear()
        self.model.addItems(models)
        self.model.setEditText(current if current in models or not models else models[0])
        self.result.setText(f"{len(models)} model(s) found." if models else "The server listed no models.")

    def _test(self):
        self._save()
        self.result.setText("Testing…")
        self.test.setEnabled(False)
        self._reply = ""
        self._req = Ask(self.settings, "Reply with one short sentence.",
                        [{"role": "user", "content": "Say hello to a PQ35 Flasher user."}], "low", self)
        self._req.text.connect(lambda t: setattr(self, "_reply", self._reply + t))
        self._req.done.connect(lambda: (self.result.setText("Works ✓  " + self._reply.strip()[:160]),
                                        self.test.setEnabled(True)))
        self._req.failed.connect(lambda m: (self.result.setText(m), self.test.setEnabled(True)))
        self._req.start()
