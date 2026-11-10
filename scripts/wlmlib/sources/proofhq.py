"""ProofHQ / Workfront Proof: proof comments and replies the person wrote (register "review").

Workfront keeps a copy of each proof comment in its own comment store ("updates"), with the text
prefixed "Proof Comment:". The public Workfront API returns all of one person's in one request,
run with their own browser session. This module never handles that session:

1. `host_from_link()` takes any proof or Workfront link the person pastes and keeps only the host.
2. `links()` builds the URLs. The person opens the host once (so their browser has a session on
   the Workfront site itself, not only inside experience.adobe.com), then opens each export link
   and saves the page (Cmd+S gives `search.json`), or copies the page text.
3. `extract_files()` reads the saved pages.

Coverage: Workfront copies most proof comments, not all (about 9 in 10 in testing). Proofs
outside Workfront (standalone ProofHQ accounts) are not covered.
"""
import datetime
import html
import json
import re
import sys
import urllib.parse

from ..clean import paste_flags
from ..common import make_record

PAGE = 2000  # the API's maximum page size
HOST_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.my\.workfront(?:\.adobe)?\.com$")
PREFIX = re.compile(r"^\s*Proof Comment:\s*", re.I)
EDITED = re.compile(r"\s*\(Edited by [^()\n]*\)\s*$")
FILTER = "ownerID=$$USER.ID&noteText=Proof%20Comment:&noteText_Mod=cicontains"
FIELDS = ("noteText,entryDate,isReply,isDeleted,documentID,threadID,parentNoteID,owner:name,"
          "parentNote:noteText,parentNote:owner:name")


def host_from_link(link):
    """Any proof review link or Workfront page link -> its Workfront host. Raises ValueError."""
    link = (link or "").strip()
    u = urllib.parse.urlsplit(link if "://" in link else "https://" + link)
    host = (u.hostname or "").lower()
    if HOST_RE.match(host):
        return host
    if host.endswith("experience.adobe.com"):
        raise ValueError("that is an experience.adobe.com link, which doesn't show your Workfront address. "
                         "Open any proof (its link starts with https://<something>.my.workfront.adobe.com/proof/) "
                         "and paste that link instead")
    raise ValueError("not a Workfront link (expected https://<something>.my.workfront.adobe.com/...). "
                     "Open any proof in Workfront and paste its link")


def links(host, pages=1):
    """The sign-in link, the count link, and one export link per page of PAGE comments."""
    base = "https://%s/attask/api/v20.0/note" % host
    return {
        "signin": "https://%s/" % host,
        "count": "%s/count?%s" % (base, FILTER),
        "pages": ["%s/search?%s&entryDate_Sort=asc&$$LIMIT=%d&$$FIRST=%d&fields=%s"
                  % (base, FILTER, PAGE, i * PAGE, FIELDS) for i in range(max(1, pages))],
    }


def parse_page(text):
    """A saved export page (raw JSON, or JSON inside an HTML <pre>) -> list of note dicts.
    Raises RuntimeError with a readable message when Workfront returned an error instead."""
    t = (text or "").strip()
    if t.startswith("<"):
        m = re.search(r"<pre[^>]*>(.*?)</pre>", t, re.S | re.I)
        t = html.unescape(m.group(1)) if m else ""
    try:
        obj = json.loads(t)
    except ValueError:
        raise RuntimeError("that file isn't a saved Workfront export (no JSON found)")
    if isinstance(obj, dict) and "error" in obj:
        msg = str((obj["error"] or {}).get("message") if isinstance(obj["error"], dict) else obj["error"])
        if "authentication" in msg.lower():
            raise RuntimeError("Workfront answered 'not signed in'. Open the sign-in link (the Workfront site itself) "
                               "first and let it finish loading, then open the export link straight away (the session expires quickly) and save it again")
        raise RuntimeError("Workfront returned an error instead of comments: %s" % msg[:200])
    data = obj.get("data") if isinstance(obj, dict) else None
    if not isinstance(data, list):
        raise RuntimeError("that file isn't a Workfront comment export (no data list)")
    return [n for n in data if isinstance(n, dict)]


def clean_text(text):
    """Note text -> the person's words: drop the 'Proof Comment:' prefix and an '(Edited by ...)' suffix."""
    t = (text or "").replace("\r\n", "\n")
    t = PREFIX.sub("", t, count=1)
    t = EDITED.sub("", t)
    return t.strip()


def wf_date(s):
    """Workfront's '2026-01-13T11:27:14:902-1000' -> ISO 8601 UTC ('' when unparseable)."""
    m = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:[:.](\d{1,3}))?([+-]\d\d:?\d\d|Z)?$", (s or "").strip())
    if not m:
        return ""
    tz = (m.group(3) or "Z").replace(":", "")
    try:
        d = datetime.datetime.strptime(m.group(1) + ("+0000" if tz == "Z" else tz), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return ""
    d = d + datetime.timedelta(milliseconds=int((m.group(2) or "0").ljust(3, "0")))
    return d.astimezone(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def records(notes, stats=None):
    """Note dicts (from one or more pages) -> records. Pure function (unit-tested)."""
    stats = stats if stats is not None else {}
    out, seen = [], set()
    for n in notes:
        nid = n.get("ID")
        if not nid or nid in seen:
            stats["duplicates"] = stats.get("duplicates", 0) + (1 if nid else 0)
            continue
        seen.add(nid)
        if n.get("isDeleted"):
            stats["deleted"] = stats.get("deleted", 0) + 1
            continue
        raw = n.get("noteText") or ""
        if not PREFIX.match(raw):  # the text filter is "contains"; keep only real proof comments
            stats["not_proof_comment"] = stats.get("not_proof_comment", 0) + 1
            continue
        text = clean_text(raw)
        if not text:
            stats["empty"] = stats.get("empty", 0) + 1
            continue
        reply = bool(n.get("isReply"))
        parent = n.get("parentNote") if isinstance(n.get("parentNote"), dict) else {}
        me = ((n.get("owner") or {}).get("name") or "").strip()
        to = ((parent.get("owner") or {}).get("name") or "").strip() if reply else ""
        if to and me and to == me:
            to = ""  # a reply in their own thread
        flags = set(paste_flags(text)) | {"proof_reply" if reply else "proof_comment"}
        out.append(make_record("proofhq", "review", text, rid="proofhq:%s" % nid, timestamp=wf_date(n.get("entryDate")),
                               recipient=to, thread=n.get("documentID") or n.get("threadID") or "",
                               context=clean_text(parent.get("noteText")) if reply else "", flags=flags))
    out.sort(key=lambda r: (r["timestamp"], r["id"]))
    stats["written"] = len(out)
    stats["replies"] = sum("proof_reply" in r["flags"] for r in out)
    return out


def extract_files(paths, stats=None):
    """Saved export pages ('-' reads stdin) -> records. Sets stats['maybe_more'] when a page was full."""
    stats = stats if stats is not None else {}
    notes = []
    for p in paths:
        if p == "-":
            text = sys.stdin.read()
        else:
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read()
        page = parse_page(text)
        if len(page) >= PAGE:
            stats["maybe_more"] = True
        notes += page
    stats["pages"] = len(paths)
    stats["read"] = len(notes)
    return records(notes, stats)
