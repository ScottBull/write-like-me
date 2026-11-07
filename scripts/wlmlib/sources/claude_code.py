"""Claude Code: the prompts the person typed (register "prompt").

Sources
  ~/.claude/history.jsonl               primary text (pastes are stored separately, so typed
                                        words stay clean)
  ~/.claude/projects/*/*.jsonl          top-level transcripts: provenance signals (promptSource,
                                        origin, entrypoint), the assistant text before each prompt,
                                        and typed prompts history lacks
  ~/.claude/projects/**/subagents/...   NOT used as prompts (written by Claude)

Removed: slash commands, bash-mode, hook/system/task injections, accepted AI suggestions, SDK /
`claude -p` runs, paste and image placeholders, and text Claude wrote that was pasted back
(>= 50% of its 8-word shingles appear in earlier Claude output).

Research finding: prompt history transfers mechanics (punctuation, capitalisation, hedging) but
not conversational vocabulary, so it is a supplementary source for chat/email, not a substitute.
"""
import bisect
import collections
import datetime as dt
import glob
import json
import os
import re

from ..clean import paste_flags
from ..common import claude_dir as default_claude_dir, make_record

SHORT_WORDS = 5          # word_count < this -> is_short
CONTEXT_CHARS = 300
MATCH_WINDOW_MS = 120_000
DUP_WINDOW_MS = 5_000
AI_DROP = 0.5            # >= this share of 8-word shingles found in Claude output -> drop
AI_FLAG = 0.15           # >= this -> keep but flag partial_ai_quote

PLACEHOLDER_RE = re.compile(
    r"\[(?:Pasted text|Image|\.\.\.Truncated text|Truncated text)\s*#\d+[^\]]{0,40}\]"
)
PASTED_TAG_RE = re.compile(r"<pasted_content\b[^>]*>.*?</pasted_content>", re.S)
SYSREM_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
ANY_BLOCK_TAGS = ("system-reminder", "user-prompt-submit-hook", "command-name",
                  "command-message", "command-args", "local-command-stdout",
                  "local-command-stderr", "local-command-caveat")
BLOCK_TAG_RE = re.compile(
    r"<(%s)\b[^>]*>.*?</\1>" % "|".join(map(re.escape, ANY_BLOCK_TAGS)), re.S)

# Leading-content markers that mean "not typed by the user".
PREFIX_REASONS = [
    ("<command-name>", "slash_command_expansion"),
    ("<command-message>", "slash_command_expansion"),
    ("<command-args>", "slash_command_expansion"),
    ("<local-command", "local_command_output"),
    ("<system-reminder", "system_reminder"),
    ("<task-notification", "task_notification"),
    ("<bash-notification", "task_notification"),
    ("<bash-input>", "bash_mode"),
    ("<bash-stdout>", "bash_mode"),
    ("<bash-stderr>", "bash_mode"),
    ("<user-prompt-submit-hook", "hook_output"),
    ("Caveat:", "meta_caveat"),
    ("[Request interrupted", "interrupt_marker"),
    ("This session is being continued", "compact_summary"),
    ("Implement the following plan:", "plan_mode_auto_prompt"),
    ("Unknown skill:", "local_command_output"),
    ("Unknown slash command:", "local_command_output"),
]
BAD_PROMPT_SOURCE = {"system": "automated_system_prompt",
                     "task-notification": "task_notification",
                     "sdk": "sdk_scripted",
                     "suggestion_accepted": "accepted_ai_suggestion"}
BAD_ORIGIN = {"task-notification": "task_notification",
              "peer": "peer_agent_message",
              "sdk": "sdk_scripted",
              "system": "automated_system_prompt"}
BAD_TURN_ORIGIN = {"task_notification": "task_notification",
                   "peer": "peer_agent_message",
                   "sdk": "sdk_scripted",
                   "system": "automated_system_prompt"}

# Slash commands whose arguments are settings, not prose.
NON_PROSE_COMMANDS = {
    "exit", "context", "model", "clear", "usage", "mcp", "login", "logout",
    "status", "stats", "effort", "permissions", "doctor", "upgrade",
    "privacy-settings", "config", "resume", "continue", "cost", "help",
    "theme", "vim", "terminal-setup", "ide", "agents", "hooks", "memory",
    "add-dir", "export", "release-notes", "bug", "fast", "output-style",
    "rewind", "todos", "insights", "statusline",
}
SLASH_RE = re.compile(r"^/([A-Za-z][\w:.-]*)(?:\s+(.*))?$", re.S)

def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000
    except Exception:
        return None


def norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def project_label(path):
    """Last path component only, so no home-directory paths end up in the pack."""
    return os.path.basename((path or "").rstrip("/"))


def content_text(content):
    """Return (text, has_tool_result, has_image)."""
    if isinstance(content, str):
        return content, False, False
    if not isinstance(content, list):
        return "", False, False
    parts, tool, img = [], False, False
    for b in content:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "tool_result":
            tool = True
        elif t == "text":
            parts.append(b.get("text", ""))
        elif t in ("image", "document"):
            img = True
    return "\n".join(parts), tool, img


def clean_typed(text):
    """Remove injected blocks and paste/image placeholders; keep typed words."""
    flags = set()
    if PASTED_TAG_RE.search(text):
        flags.add("had_paste")
        text = PASTED_TAG_RE.sub(" ", text)
    if BLOCK_TAG_RE.search(text):
        flags.add("stripped_injected_block")
        text = BLOCK_TAG_RE.sub(" ", text)
    for m in PLACEHOLDER_RE.findall(text):
        flags.add("had_image" if m.startswith("[Image") else "had_paste")
    text = PLACEHOLDER_RE.sub(" ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip(), flags


def prefix_reason(text):
    s = text.lstrip()
    for pre, reason in PREFIX_REASONS:
        if s.startswith(pre):
            return reason
    return None


# ---------------------------------------------------------------- transcripts
def load_transcripts(claude_dir, stats, asst_by_sid):
    """Return list of user-record candidates (deduped by uuid).

    Also fills asst_by_sid: sessionId -> [(ts_ms, assistant_text)] for time-based
    context lookup when a history entry has no matching transcript record.
    """
    files = sorted(glob.glob(os.path.join(claude_dir, "projects", "*", "*.jsonl")))
    sub = glob.glob(os.path.join(claude_dir, "projects", "**", "subagents", "**", "*.jsonl"),
                    recursive=True)
    stats["transcript_files"] = len(files)
    stats["subagent_files_skipped"] = len(sub)
    seen, out = set(), []
    for f in files:
        last_assistant = ""
        try:
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    stats["transcript_bad_lines"] += 1
                    continue
                typ = r.get("type")
                if r.get("isSidechain"):
                    if typ == "user":
                        stats["t_excluded"]["sidechain_subagent"] += 1
                    continue
                msg = r.get("message") or {}
                if typ == "assistant":
                    txt, _, _ = content_text(msg.get("content"))
                    if txt.strip():
                        last_assistant = txt.strip()
                        ats = parse_ts(r.get("timestamp", ""))
                        if ats is not None:
                            asst_by_sid[r.get("sessionId")].append((ats, last_assistant[:CONTEXT_CHARS]))
                    continue
                if typ != "user":
                    continue
                text, has_tool, has_img = content_text(msg.get("content"))
                if has_tool or r.get("toolUseResult") is not None:
                    stats["t_excluded"]["tool_result"] += 1
                    continue
                uid = r.get("uuid")
                if uid in seen:
                    stats["t_excluded"]["duplicate_uuid_resumed_session"] += 1
                    continue
                seen.add(uid)
                reason = None
                if r.get("isMeta") or r.get("isVisibleInTranscriptOnly"):
                    reason = "meta_injected"
                elif r.get("isCompactSummary"):
                    reason = "compact_summary"
                elif r.get("planContent"):
                    reason = "plan_mode_auto_prompt"
                else:
                    reason = prefix_reason(text)
                ps = r.get("promptSource")
                ok = (r.get("origin") or {}).get("kind") if isinstance(r.get("origin"), dict) else None
                to = r.get("turnOrigin")
                if not reason:
                    if ps in BAD_PROMPT_SOURCE:
                        reason = BAD_PROMPT_SOURCE[ps]
                    elif ok in BAD_ORIGIN:
                        reason = BAD_ORIGIN[ok]
                    elif to in BAD_TURN_ORIGIN:
                        reason = BAD_TURN_ORIGIN[to]
                    elif r.get("entrypoint") == "sdk-cli":
                        reason = "sdk_scripted"
                    elif r.get("sessionKind") == "bg" and ps != "typed":
                        reason = "background_session"
                out.append({
                    "uuid": uid, "sid": r.get("sessionId"), "ts": parse_ts(r.get("timestamp", "")),
                    "cwd": r.get("cwd", ""), "text": text, "has_img": has_img,
                    "reason": reason, "promptSource": ps,
                    "context": last_assistant[:CONTEXT_CHARS], "matched": False,
                })
    return out


# ------------------------------------------------------- AI-authored detection
SHINGLE = 8
TOKEN_RE = re.compile(r"[a-z0-9']+")


def shingles(text):
    toks = TOKEN_RE.findall(text.lower())
    return {hash(tuple(toks[i:i + SHINGLE])) for i in range(len(toks) - SHINGLE + 1)}


def iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_strings(v)


def ai_overlap(claude_dir, records):
    """For each record, fraction of its 8-word shingles that also occur in anything
    Claude produced (assistant text and tool_use inputs, incl. subagents). Catches
    Claude-written handoff/"next session" prompts the user pasted back in."""
    wanted = set()
    per = []
    for r in records:
        sh = shingles(r["text"])
        per.append(sh)
        wanted |= sh
    found = {}   # shingle -> earliest timestamp it appeared in Claude output
    files = glob.glob(os.path.join(claude_dir, "projects", "**", "*.jsonl"), recursive=True)
    for f in files:
        try:
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"assistant"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("type") != "assistant":
                    continue
                ats = parse_ts(r.get("timestamp", "")) or float("inf")
                for b in (r.get("message") or {}).get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        src = [b.get("text", "")]
                    elif b.get("type") == "tool_use":
                        src = list(iter_strings(b.get("input")))
                    else:
                        continue
                    for t in src:
                        if len(t) < 40:
                            continue
                        toks = TOKEN_RE.findall(t.lower())
                        for i in range(len(toks) - SHINGLE + 1):
                            h = hash(tuple(toks[i:i + SHINGLE]))
                            if h in wanted and ats < found.get(h, float("inf")):
                                found[h] = ats
    # only Claude output written BEFORE the user's message counts (so Claude quoting
    # the user afterwards, e.g. in a subagent prompt, doesn't disqualify the user)
    return [(sum(1 for h in sh if found.get(h, float("inf")) < r["ts"]) / len(sh)) if sh else 0.0
            for sh, r in zip(per, records)]


# --------------------------------------------------------------------- history
def compat(display, ttext):
    segs = [norm(s) for s in PLACEHOLDER_RE.split(display)]
    segs = [s for s in segs if s]
    if not segs:
        return True
    tn = norm(SYSREM_RE.sub(" ", ttext))
    return all(s in tn for s in segs)


def extract(claude_dir=None, ai_overlap_check=True, stats=None):
    """Return a list of records (register "prompt")."""
    claude_dir = claude_dir or default_claude_dir()
    stats = stats if stats is not None else collections.defaultdict(int)
    stats.setdefault("t_excluded", collections.Counter())
    stats.setdefault("h_excluded", collections.Counter())
    asst_by_sid = collections.defaultdict(list)
    T = load_transcripts(claude_dir, stats, asst_by_sid)
    for v in asst_by_sid.values():
        v.sort()
    asst_keys = {k: [x[0] for x in v] for k, v in asst_by_sid.items()}

    def context_by_time(sid, ts):
        v = asst_by_sid.get(sid)
        if not v:
            return ""
        i = bisect.bisect_left(asst_keys[sid], ts) - 1
        return v[i][1] if i >= 0 else ""

    by_sid = collections.defaultdict(list)
    by_cwd = collections.defaultdict(list)
    for t in T:
        if t["ts"] is None:
            continue
        by_sid[t["sid"]].append(t)
        by_cwd[t["cwd"]].append(t)
    for d in (by_sid, by_cwd):
        for v in d.values():
            v.sort(key=lambda x: x["ts"])
    keys_sid = {k: [x["ts"] for x in v] for k, v in by_sid.items()}
    keys_cwd = {k: [x["ts"] for x in v] for k, v in by_cwd.items()}

    def find_match(pool, keys, ts, display):
        if not pool or ts is None:
            return None
        lo = bisect.bisect_left(keys, ts - MATCH_WINDOW_MS)
        hi = bisect.bisect_right(keys, ts + MATCH_WINDOW_MS)
        best = None
        for c in pool[lo:hi]:
            if c["matched"]:
                continue
            ok = compat(display, c["text"])
            if not ok and abs(c["ts"] - ts) > 3000:
                continue
            score = (0 if ok else 1, abs(c["ts"] - ts))
            if best is None or score < best[0]:
                best = (score, c)
        return best[1] if best else None

    H = []
    hist_path = os.path.join(claude_dir, "history.jsonl")
    if os.path.exists(hist_path):
        with open(hist_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    H.append(json.loads(line))
                except ValueError:
                    stats["history_bad_lines"] += 1
    stats["history_lines"] = len(H)

    records, seen_hist = [], set()
    for h in H:
        disp = h.get("display") or ""
        ts = h.get("timestamp")
        sid = h.get("sessionId") or ""
        proj = h.get("project") or ""
        key = (ts, sid, disp)
        if key in seen_hist:
            stats["h_excluded"]["exact_duplicate_line"] += 1
            continue
        seen_hist.add(key)
        flags = set()
        s = disp.strip()
        if not s:
            stats["h_excluded"]["empty"] += 1
            continue
        if s.startswith("!"):
            stats["h_excluded"]["bash_mode_command"] += 1
            continue
        m = SLASH_RE.match(s)
        if m and "/" not in m.group(1):
            cmd, rest = m.group(1).lower(), (m.group(2) or "").strip()
            if cmd in NON_PROSE_COMMANDS or len(rest.split()) < 3:
                stats["h_excluded"]["slash_command"] += 1
                continue
            flags.add("slash_args")
            s = rest
        if sid and sid in by_sid:
            c = find_match(by_sid[sid], keys_sid[sid], ts, s)
        else:
            c = find_match(by_cwd.get(proj), keys_cwd.get(proj), ts, s) if proj in by_cwd else None
        if c:
            c["matched"] = True
            if c["reason"]:  # any non-user origin the transcript records (meta, summary, hook, ...)
                stats["h_excluded"]["transcript_says_" + c["reason"]] += 1
                continue
        else:
            r0 = prefix_reason(s)
            if r0:
                stats["h_excluded"]["prefix_" + r0] += 1
                continue
        ctx = c["context"] if c else (context_by_time(sid, ts) if sid and ts else "")
        text, f2 = clean_typed(s)
        flags |= f2
        for v in (h.get("pastedContents") or {}).values():
            flags.add("had_image" if isinstance(v, dict) and v.get("type") == "image" else "had_paste")
        if not text:
            stats["h_excluded"]["paste_or_image_only"] += 1
            continue
        records.append({"ts": ts or 0, "sid": sid or (c["sid"] if c else ""), "project": project_label(proj),
                        "text": text, "flags": flags, "context": ctx})

    for c in T:
        if c["matched"]:
            continue
        if c["reason"]:
            stats["t_excluded"][c["reason"]] += 1
            continue
        if c["ts"] is None:
            continue
        text, flags = clean_typed(c["text"])
        if not text:
            continue
        m = SLASH_RE.match(text)
        if m and "/" not in m.group(1):
            continue
        flags.add("transcript_only")
        if c["has_img"]:
            flags.add("had_image")
        records.append({"ts": c["ts"], "sid": c["sid"], "project": project_label(c["cwd"]),
                        "text": text, "flags": flags, "context": c["context"]})

    records.sort(key=lambda r: (r["sid"], r["ts"]))
    final, last = [], {}
    for r in records:
        k = (r["sid"], norm(r["text"]))
        if k in last and abs(r["ts"] - last[k]) <= DUP_WINDOW_MS:
            stats["dedup_dropped"] += 1
            continue
        last[k] = r["ts"]
        final.append(r)
    final.sort(key=lambda r: r["ts"])

    if ai_overlap_check and final:
        ov = ai_overlap(claude_dir, final)
        kept = []
        for r, o in zip(final, ov):
            if o >= AI_DROP:
                stats["ai_copy_dropped"] += 1
                continue
            if o >= AI_FLAG:
                r["flags"].add("partial_ai_quote")
            kept.append(r)
        final = kept

    out = []
    for r in final:
        r["flags"] |= paste_flags(r["text"])
        # had_paste / had_image / slash_args are informational, not exclusions
        out.append(make_record(
            "claude_code", "prompt", r["text"],
            rid="cc:" + short_id(r), timestamp=iso(r["ts"]) if r["ts"] else "",
            recipient="claude", thread=r["sid"], context=r["context"], flags=r["flags"],
            project=r["project"]))
    stats["written"] = len(out)
    return out


def short_id(r):
    import hashlib
    return hashlib.sha1(("%s|%s|%s" % (r["sid"], r["ts"], r["text"])).encode("utf-8")).hexdigest()[:16]
