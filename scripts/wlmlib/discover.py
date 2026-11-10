"""Find which writing sources exist on this machine. Reads metadata only (existence, counts)."""
import os
import platform
import sys

from .common import claude_dir, pack_dir
from .sources import imessage, outlook_mac, teams


def _count_lines(p, cap=2_000_000):
    n = 0
    try:
        with open(p, "rb") as f:
            for _ in f:
                n += 1
                if n >= cap:
                    break
    except OSError:
        return None
    return n


def discover():
    out = {"platform": sys.platform, "python": platform.python_version(), "pack": pack_dir(),
           "pack_exists": os.path.exists(os.path.join(pack_dir(), "stats.json")), "sources": {}}
    S = out["sources"]
    hist = os.path.join(claude_dir(), "history.jsonl")
    n = _count_lines(hist) if os.path.exists(hist) else None
    S["claude_code"] = {"available": bool(n), "register": "prompt",
                        "detail": ("%d history entries" % n) if n else "no %s" % hist}
    ok, why = teams.available()
    cache = sys.platform == "darwin" and os.path.isdir(os.path.join(teams.idb_dir(), teams.STEM + ".leveldb"))
    S["teams"] = {"available": ok, "register": "chat", "detail": why, "cache_found": bool(cache),
                  "needs_install": bool(cache and not ok and "not installed" in why)}
    ok, why = outlook_mac.available()
    S["outlook_mac"] = {"available": ok, "register": "email", "detail": why}
    ok, why = imessage.available()
    chatdb = os.path.join(os.path.expanduser("~"), "Library", "Messages", "chat.db")
    readable = False
    if sys.platform == "darwin" and os.path.exists(chatdb):
        try:
            with open(chatdb, "rb") as f:
                f.read(16)
            readable = True
        except OSError:
            readable = False
    S["imessage"] = {"available": ok and readable, "register": "chat",
                     "detail": why if not ok else ("ok" if readable else
                                                   "needs Full Disk Access for your terminal app")}
    S["proofhq"] = {"available": True, "register": "review",
                    "detail": "ProofHQ / Workfront proof comments: you open a link in your signed-in browser and save the page"}
    S["files"] = {"available": True, "register": "email/doc",
                  "detail": "a folder of .txt/.md/.eml/.mbox you choose (e.g. a Sent-mail export)"}
    S["paste"] = {"available": True, "register": "any", "detail": "samples you paste in"}
    return out
