"""Non-interactive model calls through the person's own Claude Code login (`claude -p`).

Used only by voice-check (briefs, scripted drafts, judge) and the `critique` fallback. Each call:
  - runs from an empty temp dir (no CLAUDE.md, memory or project files); the prompt goes on stdin
  - replaces the system prompt and disables every tool, MCP server, skill and setting source
  - goes to whatever account/model the person's Claude Code uses ($WRITE_LIKE_ME_MODEL overrides)
Text sent: the prompt shown, which includes real messages from the bank. Nothing else.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time

from .common import private_open

ENV_OVERRIDES = {
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_ATTACHMENTS": "1",
}


def split_template(path):
    """Template files have '=== SYSTEM ===' and '=== USER ===' sections."""
    with open(path, encoding="utf-8") as f:
        t = f.read()
    sys_part, user_part = t.split("=== USER ===", 1)
    return sys_part.replace("=== SYSTEM ===", "", 1).strip(), user_part.strip()


def fill(tmpl, **kw):
    out = tmpl
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def available():
    return shutil.which("claude") is not None


def call(system, user, cache_dir=None, model=None, retries=2, timeout=600):
    """Return the model's text. Cached by content hash in cache_dir when given."""
    model = model or os.environ.get("WRITE_LIKE_ME_MODEL") or ""
    key = hashlib.sha256(json.dumps([model, system, user]).encode("utf-8")).hexdigest()[:24]
    if cache_dir:
        os.makedirs(cache_dir, mode=0o700, exist_ok=True)
        cp = os.path.join(cache_dir, key + ".json")
        if os.path.exists(cp):
            with open(cp, encoding="utf-8") as f:
                return json.load(f)["text"]
    if not available():
        raise RuntimeError("the `claude` CLI is not on PATH")
    env = dict(os.environ)
    env.update(ENV_OVERRIDES)
    cmd = ["claude", "-p", "--tools", "", "--system-prompt", system, "--setting-sources", "",
           "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
           "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    # The prompt (which holds real messages) goes on stdin, not argv: argv is visible to other
    # local users in `ps` and is limited by ARG_MAX. The system prompts are fixed template text.
    last = ""
    for attempt in range(retries + 1):
        empty = tempfile.mkdtemp(prefix="wlm_call_")
        try:
            p = subprocess.run(cmd, cwd=empty, env=env, input=user, capture_output=True,
                               text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            last = "timeout"
            continue
        finally:
            shutil.rmtree(empty, ignore_errors=True)
        if p.returncode == 0:
            try:
                d = json.loads(p.stdout)
                text = (d.get("result") or "").strip()
            except ValueError:
                text = p.stdout.strip()
            if text:
                if cache_dir:
                    with private_open(cp) as f:
                        json.dump({"text": text}, f)
                return text
        last = (p.stderr or p.stdout or "").strip()[:300]
        time.sleep(2 * (attempt + 1))
    raise RuntimeError("claude -p failed: %s" % last)
