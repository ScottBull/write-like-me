"""One draft call: the assembled context file as the system prompt, the task on stdin.

This is the archive pilot's call shape (`claude -p --system-prompt-file`), used by `wlm draft`
(the scripted path, and the fallback when the voice-writer subagent is unavailable) and by
voice-check. The system file is the output of `select.assemble()` rendered from
`templates/context.md`: a preface, the person's own entries in date order, the "write the next
entry" framing. Passing it as a file keeps tens of thousands of tokens of real messages out of argv
and lets the CLI's system-prompt cache breakpoint cover it, so repeat drafts within the cache
window read it instead of paying for it again.

Each call runs from an empty temp dir with every tool, MCP server, slash command and setting
source disabled, in `llm.child_env()` (no ANTHROPIC_API_KEY unless WRITE_LIKE_ME_KEEP_API_KEY=1).
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time

from .common import private_open
from .llm import available, child_env


def command(system_file, model=None):
    """The argv of one draft call. Holds only the path of the system file, never its text."""
    cmd = ["claude", "-p", "--tools", "", "--system-prompt-file", os.path.abspath(system_file),
           "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands",
           "--no-session-persistence", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    return cmd


def run(system_file, task, model=None, cache_dir=None, sample=0, retries=2, timeout=900):
    """One draft call. Returns dict(text, usage, cost_usd, duration_ms, cached).

    `sample` only varies the cache key, so voice-check can take several samples of one item."""
    model = model or os.environ.get("WRITE_LIKE_ME_MODEL") or ""
    if not os.path.isfile(system_file):
        raise RuntimeError("no context file at %s" % system_file)
    with open(system_file, "rb") as f:
        sys_hash = hashlib.sha256(f.read()).hexdigest()
    cp = None
    if cache_dir:
        os.makedirs(cache_dir, mode=0o700, exist_ok=True)
        key = hashlib.sha256(json.dumps([model, sys_hash, task, sample]).encode("utf-8")).hexdigest()[:24]
        cp = os.path.join(cache_dir, "draft-" + key + ".json")
        if os.path.exists(cp):
            with open(cp, encoding="utf-8") as f:
                d = json.load(f)
            d["cached"] = True
            return d
    if not available():
        raise RuntimeError("the `claude` CLI is not on PATH")
    cmd = command(system_file, model)
    env = child_env()
    last = ""
    for attempt in range(retries + 1):
        empty = tempfile.mkdtemp(prefix="wlm_draft_")
        try:
            # The task (which holds the person's notes and the message they reply to) goes on
            # stdin, never argv: argv is visible to other local users in `ps`.
            p = subprocess.run(cmd, cwd=empty, env=env, input=task, capture_output=True,
                               text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            last = "timeout"
            continue
        finally:
            shutil.rmtree(empty, ignore_errors=True)
        try:
            d = json.loads(p.stdout)
        except ValueError:
            d = {}
        if not isinstance(d, dict):
            d = {}
        text = (d.get("result") or "").strip()
        if p.returncode == 0 and text and not d.get("is_error"):
            out = {"text": text, "usage": d.get("usage"), "cost_usd": d.get("total_cost_usd"),
                   "duration_ms": d.get("duration_ms"), "model": model, "sample": sample}
            if cp:
                with private_open(cp) as f:
                    json.dump(out, f)
            out["cached"] = False
            return out
        last = (p.stderr or p.stdout or "").strip()[:300]
        time.sleep(2 * (attempt + 1))
    raise RuntimeError("claude -p failed: %s" % last)


def draft(system_file, task, model=None, **kw):
    """Return the drafted text for `task` with `system_file` as the system prompt."""
    return run(system_file, task, model=model, **kw)["text"]
