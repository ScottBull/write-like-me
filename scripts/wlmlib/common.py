"""Shared helpers: pack location, JSONL I/O, record schema."""
import datetime as dt
import hashlib
import json
import os
import re

REGISTERS = ("chat", "email", "doc", "prompt")
REGISTER_HELP = {
    "chat": "short messages: Teams, Slack, iMessage, SMS",
    "email": "email you sent",
    "doc": "longer prose: documents, notes, posts",
    "prompt": "what you type to AI assistants (Claude Code)",
}

# Record schema shared by every extractor (one JSON object per line):
#   id         stable id, prefixed by source ("teams:...", "eml:...")
#   source     extractor name
#   register   one of REGISTERS
#   timestamp  ISO 8601 UTC, or "" if unknown
#   text       the person's own words only (quotes, signatures, pastes removed)
#   recipient  who it was written to (display name / address), "" if unknown
#   thread     conversation key (chat id, subject, session id, file name)
#   context    excerpt of the message it replied to (<= 300 chars), "" if none
#   flags      list of strings; see build.EXCLUDE_FLAGS
RECORD_KEYS = ("id", "source", "register", "timestamp", "text", "recipient",
               "thread", "context", "flags")


def claude_dir():
    """Claude Code's config dir: $CLAUDE_CONFIG_DIR or ~/.claude (where history.jsonl lives)."""
    return os.path.abspath(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join("~", ".claude")))


def pack_dir():
    """Voice-pack directory: $WRITE_LIKE_ME_PACK or ~/.claude/voice-pack."""
    p = os.environ.get("WRITE_LIKE_ME_PACK") or os.path.join("~", ".claude", "voice-pack")
    return os.path.abspath(os.path.expanduser(p))


def ensure_pack(path=None):
    path = path or pack_dir()
    os.makedirs(os.path.join(path, "sources"), mode=0o700, exist_ok=True)
    secure_tree(path)
    return path


def secure_tree(path):
    """Make the pack readable only by its owner: directories 0700, files 0600 (also repairs packs
    built by older versions). The vendor folder (third-party code) is left alone."""
    try:
        os.chmod(path, 0o700)
    except OSError:
        return
    for root, dirs, files in os.walk(path):
        if os.path.abspath(root) == os.path.abspath(path):
            dirs[:] = [d for d in dirs if d != "vendor"]
        for d in dirs:
            try:
                os.chmod(os.path.join(root, d), 0o700)
            except OSError:
                pass
        for f in files:
            try:
                os.chmod(os.path.join(root, f), 0o600)
            except OSError:
                pass


def private_open(path, mode="w"):
    """open() for pack files: created 0600 (not umask-dependent), parent dirs 0700."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, mode=0o700, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if "a" in mode else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except (OSError, AttributeError):
        pass
    return os.fdopen(fd, mode, encoding="utf-8")


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):  # valid JSON that isn't an object (null, [], "x") is malformed
                out.append(row)
    return out


def write_jsonl(path, rows):
    tmp = path + ".tmp"
    with private_open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def append_jsonl(path, row):
    with private_open(path, "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    tmp = path + ".tmp"
    with private_open(tmp, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def short_hash(*parts, n=16):
    h = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return h[:n]


def iso_from_ms(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def iso_from_s(s):
    return dt.datetime.fromtimestamp(s, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(s):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def make_record(source, register, text, rid=None, timestamp="", recipient="", thread="",
                context="", flags=None, **extra):
    rec = {
        "id": rid or "%s:%s" % (source, short_hash(source, timestamp, thread, text)),
        "source": source,
        "register": register,
        "timestamp": timestamp or "",
        "text": text,
        "recipient": recipient or "",
        "thread": thread or "",
        "context": (context or "")[:300],
        "flags": sorted(set(flags or [])),
    }
    rec.update(extra)
    return rec


def norm_space(s):
    return re.sub(r"\s+", " ", s or "").strip()
