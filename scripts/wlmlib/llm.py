"""Non-interactive model calls through the person's own Claude Code login (`claude -p`).

Used by voice-check (briefs, judge) and, through `draft.py`, by scripted drafts. Each call:
  - runs from an empty temp dir (no CLAUDE.md, memory or project files); the prompt goes on stdin
  - replaces the system prompt and disables every tool, MCP server, skill and setting source
  - runs with `child_env()`: the person's environment minus ANTHROPIC_API_KEY (which `claude -p`
    would bill instead of their login; WRITE_LIKE_ME_KEEP_API_KEY=1 keeps it) and the parent
    session's messaging socket; cloud-provider and gateway settings pass through
  - uses the person's default model ($WRITE_LIKE_ME_MODEL overrides)
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
    "CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1",
}

# Never passed to a child `claude`. With ANTHROPIC_API_KEY set, non-interactive `claude -p` prefers
# the key over the claude.ai login, so the call would be billed to an API account instead of the
# person's plan; set WRITE_LIKE_ME_KEEP_API_KEY=1 to keep it (for people whose Claude Code runs on an
# API key). The messaging pair belongs to the parent session and must not be inherited.
# Everything else is passed through, including the Bedrock / Vertex / Foundry switches, base URLs and
# auth tokens: people whose Claude Code runs through a cloud provider or a gateway need them, and
# for them that is the login.
ENV_DROP = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
)
KEEP_API_KEY = "WRITE_LIKE_ME_KEEP_API_KEY"


def child_env(base=None):
    """The environment for a child `claude`: `base` (default os.environ) without ENV_DROP (the API
    key stays when WRITE_LIKE_ME_KEEP_API_KEY=1), plus ENV_OVERRIDES."""
    env = dict(os.environ if base is None else base)
    keep_key = env.get(KEEP_API_KEY, "").strip() == "1"
    for k in ENV_DROP:
        if k == "ANTHROPIC_API_KEY" and keep_key:
            continue
        env.pop(k, None)
    env.update(ENV_OVERRIDES)
    return env


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


def claude_bin():
    """The Claude Code CLI to run: $WRITE_LIKE_ME_CLAUDE (a wrapper script, say) or `claude`."""
    return os.environ.get("WRITE_LIKE_ME_CLAUDE") or "claude"


def available():
    return shutil.which(claude_bin()) is not None


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
    env = child_env()
    cmd = [claude_bin(), "-p", "--tools", "", "--system-prompt", system, "--setting-sources", "",
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
