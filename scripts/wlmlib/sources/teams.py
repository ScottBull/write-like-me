"""Microsoft Teams ("new Teams", macOS): the person's own sent chat messages (register "chat").

Reads the local WebView2 IndexedDB cache, which holds only what Teams has cached (often weeks to
months, not full history). The database is copied to a temp dir first and only the copy is
parsed; Teams itself is never touched.

Needs the third-party parser ccl_chromium_reader (MIT, CCL Solutions Group), which is NOT bundled.
Optional install (upstream says Python 3.10+; the module used here also loads on 3.9), at the
pinned commits below, into the pack's vendor folder:

    python3 -I scripts/wlm.py vendor-install teams          # shows exactly what it would run
    python3 -I scripts/wlm.py vendor-install teams --yes    # runs it

The vendor dir can be changed with $WRITE_LIKE_ME_VENDOR. The package __init__ is bypassed, so its
optional Brotli/zstd dependencies are not needed.
"""
import collections
import datetime
import html
import os
import re
import shutil
import sys
import tempfile
import types

from ..clean import html_to_text, paste_flags
from ..common import make_record, pack_dir, short_hash

TEAMS_IDB = os.path.join("~", "Library", "Containers", "com.microsoft.teams2", "Data", "Library",
                         "Application Support", "Microsoft", "MSTeams", "EBWebView", "WV2Profile_tfw",
                         "IndexedDB")
STEM = "https_teams.microsoft.com_0.indexeddb"


# Third-party code fetched by `wlm vendor-install teams`, pinned to the commits this plugin was
# tested with (2026-11-06), so a later upstream change can't silently alter what runs.
VENDOR = [("ccl_chromium_reader", "https://github.com/cclgroupltd/ccl_chromium_reader",
           "ad37443541dd91dac3ac1fb2eecaeb8693f36815"),
          ("ccl_simplesnappy", "https://github.com/cclgroupltd/ccl_simplesnappy",
           "3d085230baa8c46cf2090ebba29bf6e8eab31087")]


def vendor_commands():
    """[(description, argv)] that install the pinned parser into vendor_dir()."""
    vd = vendor_dir()
    cmds = []
    for name, url, sha in VENDOR:
        d = os.path.join(vd, name)
        cmds += [(name, ["git", "init", "-q", d]),
                 (name, ["git", "-C", d, "fetch", "-q", "--depth", "1", url, sha]),
                 (name, ["git", "-C", d, "checkout", "-q", "FETCH_HEAD"])]
    return cmds


def idb_dir():
    return os.path.expanduser(TEAMS_IDB)


def vendor_dir():
    return os.path.expanduser(os.environ.get("WRITE_LIKE_ME_VENDOR") or os.path.join(pack_dir(), "vendor"))


def available():
    """(ok, reason)."""
    if sys.platform != "darwin":
        return False, "Teams cache extraction is macOS-only"
    if not os.path.isdir(os.path.join(idb_dir(), STEM + ".leveldb")):
        return False, "new-Teams cache not found"
    vd = vendor_dir()
    if not (os.path.isdir(os.path.join(vd, "ccl_chromium_reader", "ccl_chromium_reader"))
            and os.path.isdir(os.path.join(vd, "ccl_simplesnappy", "ccl_simplesnappy"))):
        return False, "ccl_chromium_reader not installed (see voice-setup instructions)"
    if load_ccl() is None:
        # Upstream declares 3.10+, but the IndexedDB module imports and parses fine on 3.9
        # (macOS's stock python3). Only blame the version if the import actually failed.
        if sys.version_info < (3, 10):
            return False, "ccl_chromium_reader failed to load; try Python 3.10+ (this is %d.%d)" % sys.version_info[:2]
        return False, "ccl_chromium_reader is installed but failed to load"
    return True, "ok"


def load_ccl():
    vd = vendor_dir()
    reader = os.path.join(vd, "ccl_chromium_reader", "ccl_chromium_reader")
    snappy = os.path.join(vd, "ccl_simplesnappy", "ccl_simplesnappy")
    if not (os.path.isdir(reader) and os.path.isdir(snappy)):
        return None
    pkg = types.ModuleType("ccl_chromium_reader")
    pkg.__path__ = [reader]
    sys.modules["ccl_chromium_reader"] = pkg
    if snappy not in sys.path:
        sys.path.insert(0, snappy)
    try:
        from ccl_chromium_reader import ccl_chromium_indexeddb as idb  # noqa
    except Exception:
        return None
    return idb


def as_str(v):
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return v.decode("latin-1")
    return v if isinstance(v, str) else ""


def is_undef(v):
    return v is None or type(v).__name__ in ("_Undefined", "Undefined")


def clean_content(content, message_type):
    content = as_str(content)
    if message_type == "Text":
        return html.unescape(content).strip()
    return html_to_text(content)


def chat_type(conv_id, conv):
    if "meeting_" in conv_id:
        return "meeting"
    if conv_id.endswith("@unq.gbl.spaces"):
        return "1:1"
    if conv_id.endswith("@thread.tacv2") or conv_id.endswith("@thread.skype"):
        return "channel"
    tt = (conv or {}).get("threadProperties") or {}
    if isinstance(tt, dict) and not is_undef(tt.get("spaceType")):
        return "channel"
    return "group"


def conv_topic(conv):
    tt = (conv or {}).get("threadProperties") or {}
    if isinstance(tt, dict):
        t = as_str(tt.get("topic"))
        if t:
            return t
    return ""


def thread_records(items, me, cid, ctype, topic):
    """items: sorted [(ts_ms, creator, display_name, text)]. Pure function (unit-tested)."""
    others = collections.Counter(n for _, c, n, t in items if c != me and n)
    recipient = others.most_common(1)[0][0] if (ctype == "1:1" and others) else (topic or ctype)
    out, prev = [], ""
    for ts, creator, _, text in items:
        if creator == me and text:
            flags = set(paste_flags(text))
            if ctype in ("channel", "meeting"):
                flags.add("teams_" + ctype)  # informational
            out.append(make_record(
                "teams", "chat", text, rid="teams:%s:%d" % (short_hash(cid, n=10), int(ts)),
                timestamp=datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc).isoformat(),
                recipient=recipient, thread=cid, context=prev[-300:], flags=flags, chat_type=ctype))
        if text:
            prev = text
    return out


def manager_dbs(names):
    """Pick the reply-chain and conversation databases out of the IndexedDB names and the user's
    own id. Raises RuntimeError (which the CLI reports) if the cache is partial or a new layout."""
    rc = [k for k in names if k.startswith("Teams:replychain-manager:react-web-client")]
    conv = [k for k in names if k.startswith("Teams:conversation-manager:react-web-client")]
    missing = [n for n, v in (("replychain-manager", rc), ("conversation-manager", conv)) if not v]
    if missing:
        raise RuntimeError("the Teams cache is missing its %s database (Teams may not have finished syncing, "
                           "or this Teams version stores data differently); open Teams, let it sync, quit it "
                           "and try again" % " and ".join(missing))
    m = re.match(r"Teams:replychain-manager:react-web-client:([0-9a-f-]{36}):([0-9a-f-]{36})", rc[0])
    if not m:
        raise RuntimeError("unrecognised Teams database name; this Teams version is not supported yet")
    return rc[0], conv[0], "8:orgid:" + m.group(2)


def extract(stats=None):
    stats = stats if stats is not None else {}
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    idb = load_ccl()
    tmp = tempfile.mkdtemp(prefix="wlm_teams_")
    try:
        for suffix in (".leveldb", ".blob"):
            src = os.path.join(idb_dir(), STEM + suffix)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(tmp, STEM + suffix), ignore=shutil.ignore_patterns("LOCK"))
        w = idb.WrappedIndexDB(os.path.join(tmp, STEM + ".leveldb"), os.path.join(tmp, STEM + ".blob"))
        dbs = {d.name: d.dbid_no for d in w.database_ids}
        rc_name, conv_name, me = manager_dbs(dbs)
        convs = {}
        for live in (True, False):
            for rec in w[dbs[conv_name]]["conversations"].iterate_records(live_only=live):
                v = rec.value
                if isinstance(v, dict) and isinstance(v.get("id"), str):
                    cur = convs.get(v["id"])
                    if cur is None or (v.get("version") or 0) > (cur.get("version") or 0):
                        convs[v["id"]] = v
        msgs = {}
        for store in ("replychains", "replychains-2"):
            try:
                st = w[dbs[rc_name]][store]
            except Exception:
                continue
            for live in (True, False):
                for rec in st.iterate_records(live_only=live):
                    v = rec.value
                    if not isinstance(v, dict) or not isinstance(v.get("messageMap"), dict):
                        continue
                    for mm in v["messageMap"].values():
                        if not isinstance(mm, dict):
                            continue
                        cid = as_str(mm.get("conversationId"))
                        if not cid.startswith("19:"):
                            continue
                        try:
                            ver = float(as_str(mm.get("version")) or 0)
                        except ValueError:
                            ver = 0.0
                        key = (cid, as_str(mm.get("id")))
                        if key not in msgs or ver > msgs[key][0]:
                            msgs[key] = (ver, mm)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    by_conv = collections.defaultdict(list)
    for (cid, _), (_, mm) in msgs.items():
        mt = as_str(mm.get("messageType"))
        if mt not in ("RichText/Html", "Text"):
            continue
        props = mm.get("properties") if isinstance(mm.get("properties"), dict) else {}
        if not is_undef(mm.get("deletionInfo")) or props.get("deletetime"):
            continue
        t = mm.get("originalArrivalTime")
        if not isinstance(t, (int, float)):
            continue
        by_conv[cid].append((t, as_str(mm.get("creator")), as_str(mm.get("imDisplayName")),
                             clean_content(mm.get("content"), mt)))
    out = []
    for cid, items in by_conv.items():
        items.sort(key=lambda x: x[0])
        conv = convs.get(cid)
        out += thread_records(items, me, cid, chat_type(cid, conv), conv_topic(conv))
    out.sort(key=lambda r: r["timestamp"])
    stats["written"] = len(out)
    stats["conversations"] = len({r["thread"] for r in out})
    return out
