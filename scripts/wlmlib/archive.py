"""The archive: the person's own cleaned, masked messages, one file per register, in date order.

pack/archive/<register>.jsonl is what the drafter reads (a budgeted selection of it goes into the
context file; see select.py). It is derived: `wlm build` rewrites it from sources/ through
build.clean_bank, so masking, flag exclusion and de-duplication are the bank's.

Each entry carries `tokens_est`, the estimated prompt cost of the rendered entry (a dated header
line, "Replying to: <context>", a label, and the text) at CHARS_PER_TOKEN, so selection can fill a
token budget without a tokenizer.
"""
import os

from .common import ARCHIVE_DIR, ARCHIVE_KEYS, REGISTERS, est_tokens, read_jsonl, write_jsonl

# Characters the renderer adds around an entry's text and context: a header such as
# "### chat 12 (1:1), 2026-03-01", "Replying to: ", "My message:", newlines and the blank line
# between entries (the pilot's format, ~55-65 characters).
ENTRY_OVERHEAD_CHARS = 60
NO_CONTEXT_CHARS = 30      # "(nothing: I started this one)" when there is no context


def entry_chars(rec):
    ctx = (rec.get("context") or "").strip()
    return (len((rec.get("text") or "").strip()) + (len(ctx) if ctx else NO_CONTEXT_CHARS)
            + ENTRY_OVERHEAD_CHARS)


def tokens_est(rec):
    return est_tokens(entry_chars(rec))


def to_entry(rec):
    """A cleaned bank record as an archive entry: the schema keys first, extras after, no flags."""
    e = {k: rec.get(k, "") for k in ARCHIVE_KEYS if k not in ("word_count", "notes", "tokens_est")}
    e["word_count"] = rec.get("word_count") or 0
    e["notes"] = list(rec.get("notes") or [])
    e["tokens_est"] = tokens_est(rec)
    for k, v in rec.items():
        if k not in e and k != "flags":
            e[k] = v
    return e


def date_key(e):
    # ISO 8601 UTC strings sort by time; unknown ("") sorts first, so it is never the "latest"
    return (e.get("timestamp") or "", e.get("id") or "")


def build_archive(bank):
    """{register: [entries in date order]} for every register present in the cleaned bank."""
    out = {}
    for rec in bank:
        if rec.get("register") in REGISTERS:
            out.setdefault(rec["register"], []).append(to_entry(rec))
    for reg in out:
        out[reg].sort(key=date_key)
    return out


def archive_path(pack, register):
    return os.path.join(pack, ARCHIVE_DIR, "%s.jsonl" % register)


def write_archive(pack, bank):
    """Write archive/<register>.jsonl for each register in the bank, remove files for registers that
    no longer have entries, and return {register: {"entries": n, "tokens": sum of tokens_est}}."""
    arch = build_archive(bank)
    d = os.path.join(pack, ARCHIVE_DIR)
    os.makedirs(d, mode=0o700, exist_ok=True)
    for f in os.listdir(d):
        if f.endswith(".jsonl") and f[:-6] not in arch:
            os.remove(os.path.join(d, f))
    summary = {}
    for reg in REGISTERS:
        if reg not in arch:
            continue
        write_jsonl(archive_path(pack, reg), arch[reg])
        summary[reg] = {"entries": len(arch[reg]), "tokens": sum(e["tokens_est"] for e in arch[reg])}
    return summary


def load_archive(pack, register=None):
    """Entries of one register (date order), or of all registers (register order, then date)."""
    regs = [register] if register else list(REGISTERS)
    out = []
    for reg in regs:
        out += read_jsonl(archive_path(pack, reg))
    return out


def has_archive(pack):
    d = os.path.join(pack, ARCHIVE_DIR)
    return os.path.isdir(d) and any(f.endswith(".jsonl") for f in os.listdir(d))
