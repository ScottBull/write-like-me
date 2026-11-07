"""iMessage / SMS via the third-party `imessage-exporter` tool (optional, macOS; register "chat").

imessage-exporter (https://github.com/ReagentX/imessage-exporter, `brew install imessage-exporter`)
decodes ~/Library/Messages/chat.db, which needs Full Disk Access for your terminal app. It
writes one .txt file per conversation; this module keeps only the lines you sent ("Me").

The txt layout this parser expects (one block per message, blank line between blocks):

    May 17, 2024  5:29:42 PM (Read by them after 1 minute)
    Me
    message text, possibly several lines

Lines it doesn't recognise are skipped, so a format change degrades to fewer messages rather
than wrong ones. You can also pass an existing export folder with --export-dir.
"""
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import tempfile

from ..clean import paste_flags
from ..common import make_record

TS_RE = re.compile(r"^([A-Z][a-z]{2} \d{1,2}, \d{4}\s+\d{1,2}:\d{2}:\d{2}\s?[AP]M)\b")
SKIP_BODY = re.compile(r"^(Tapbacks?:|Attachment:|.*\.(?:heic|jpe?g|png|mov|mp4|gif|caf|pdf)$|"
                       r"This message responded to an earlier message\.|Edited \d+)", re.I)
TAPBACK_HDR = re.compile(r"^(?:Tapbacks?|Reactions?):\s*$", re.I)
# A reaction sent as a plain-text SMS/RCS message: the verb, then the quoted message it reacts to
# ("Loved “see you at 6”", 'Reacted 👍 to "ok"'). Matched against the whole body, so an ordinary
# message that starts with the same word ("Loved your talk today!") is kept.
REACTION = re.compile(r"^(?:(?:Loved|Liked|Disliked|Laughed at|Emphasi[sz]ed|Questioned)|"
                      r"Removed an? .{1,20} from|Reacted .{1,12} to)\s+[“\"].*[”\"]\s*$", re.S)


def available():
    if sys.platform != "darwin":
        return False, "iMessage extraction is macOS-only"
    if not shutil.which("imessage-exporter"):
        return False, "imessage-exporter not installed (optional: brew install imessage-exporter)"
    return True, "ok"


def parse_export_file(text, conversation):
    """Return [(timestamp_iso, text, context)] for messages sent by 'Me'. Pure (unit-tested)."""
    lines = text.replace("\r\n", "\n").split("\n")
    blocks, cur = [], None
    for ln in lines:
        m = TS_RE.match(ln.strip())
        if m:
            if cur:
                blocks.append(cur)
            cur = {"ts": m.group(1), "sender": None, "body": []}
            continue
        if cur is None:
            continue
        if cur["sender"] is None:
            if ln.strip():
                cur["sender"] = ln.strip()
            continue
        cur["body"].append(ln)
    if cur:
        blocks.append(cur)
    out, prev = [], ""
    for b in blocks:
        body, in_tb = [], False
        for l in b["body"]:
            if TAPBACK_HDR.match(l.strip()):
                in_tb = True  # the indented "Loved by Jordan" lines under it are not message text
                continue
            if in_tb and (l[:1].isspace() or not l.strip()):
                continue
            in_tb = False
            if l.strip() and not SKIP_BODY.match(l.strip()):
                body.append(l)
        txt = "\n".join(body).strip()
        if not txt or REACTION.match(txt):
            continue
        if (b["sender"] or "").lower() == "me":
            try:
                d = dt.datetime.strptime(re.sub(r"\s+", " ", b["ts"]), "%b %d, %Y %I:%M:%S %p")
                ts = d.isoformat()
            except ValueError:
                ts = ""
            out.append((ts, txt, prev[-300:]))
        prev = txt
    return out


def extract(export_dir=None, start_date=None, stats=None):
    """The exporter writes every conversation (other people's messages too) to a private temp dir;
    it is always removed, including on timeout, error or Ctrl-C."""
    stats = stats if stats is not None else {}
    tmp = None
    out = []
    try:
        if not export_dir:
            ok, why = available()
            if not ok:
                raise RuntimeError(why)
            tmp = tempfile.mkdtemp(prefix="wlm_imsg_")
            cmd = ["imessage-exporter", "-f", "txt", "-o", tmp]
            if start_date:
                cmd += ["-s", start_date]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            except subprocess.TimeoutExpired:
                raise RuntimeError("imessage-exporter timed out after 30 minutes; try --since YYYY-MM-DD")
            if res.returncode != 0:
                raise RuntimeError("imessage-exporter failed (Full Disk Access for your terminal?): "
                                   + (res.stderr or res.stdout).strip()[:300])
            export_dir = tmp
        for root, _, files in os.walk(os.path.expanduser(export_dir)):
            for f in sorted(files):
                if not f.endswith(".txt"):
                    continue
                conv = f[:-4]
                with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                    rows = parse_export_file(fh.read(), conv)
                for ts, txt, ctx in rows:
                    out.append(make_record("imessage", "chat", txt, timestamp=ts, recipient=conv,
                                           thread=conv, context=ctx, flags=paste_flags(txt)))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    stats["written"] = len(out)
    return out
