"""Outlook for Mac ("legacy" Outlook): the person's own sent email (register "email").

1. Copy Outlook.sqlite (+ -wal/-shm) to a temp dir and open the copy read-only. Pick Mail rows
   whose sender address is one of the person's own addresses (from the account tables, or
   --address), skipping drafts, outbox, junk, deleted and chat-history folders (by Outlook's
   language-independent folder type, with names as a fallback) and anything with no sent time.
2. Fetch subject + plain-text body for those rows from the running Outlook via AppleScript
   (read-only; macOS asks once for Automation permission).
3. Clean: drop quoted replies, forwards, signatures, disclaimers and meeting boilerplate.

"New Outlook" keeps no local message store; export Sent Items as .eml files (drag them to a
folder) and use the `files` source instead. The local cache often covers only recent months.
"""
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from ..clean import clean_email_body, parent_excerpt, split_parent
from ..common import make_record

PROFILE = os.path.join("~", "Library", "Group Containers", "UBF8T346G9.Office", "Outlook",
                       "Outlook 15 Profiles", "Main Profile", "Data")
SKIP_FOLDERS = {"Migrated Teams Chat", "Conversation History", "Drafts", "Outbox", "Junk Email",
                "Junk E-mail", "Sync Issues", "Conflicts", "Local Failures", "Server Failures",
                "Deleted Items",
                # localized Drafts / Outbox / Junk / Deleted names, for profiles without folder types
                "Entwürfe", "Postausgang", "Junk-E-Mail", "Gelöschte Elemente", "Brouillons",
                "Boîte d'envoi", "Courrier indésirable", "Éléments supprimés", "Borradores",
                "Bandeja de salida", "Correo no deseado", "Elementos eliminados", "Bozze",
                "Posta in uscita", "Posta indesiderata", "Elementi eliminati", "Rascunhos",
                "Caixa de Saída", "Lixo Eletrônico", "Itens Excluídos", "Concepten", "Postvak UIT",
                "Ongewenste e-mail", "Verwijderde items", "下書き", "送信トレイ", "迷惑メール", "削除済みアイテム"}
# Folders.Folder_SpecialFolderType values (language-independent): 2 Outbox, 9 Deleted Items,
# 10 Drafts, 12 Junk Email, 103 temporary items.
SKIP_SPECIAL_TYPES = {2, 9, 10, 12, 103}
FS, RS = "\x1f", "\x1e"
APPLESCRIPT = r'''
on run argv
    set FS to (ASCII character 31)
    set RS to (ASCII character 30)
    set outText to ""
    tell application "Microsoft Outlook"
        repeat with idStr in argv
            try
                set m to message id (idStr as integer)
                set subj to ""
                try
                    set subj to subject of m
                end try
                if subj is missing value then set subj to ""
                set bodyTxt to ""
                try
                    set bodyTxt to plain text content of m
                end try
                if bodyTxt is missing value then set bodyTxt to ""
                set isMtg to false
                try
                    set isMtg to is meeting of m
                end try
                set outText to outText & idStr & FS & subj & FS & (isMtg as string) & FS & bodyTxt & RS
            on error errMsg
                set outText to outText & idStr & FS & "" & FS & "error" & FS & "" & RS
            end try
        end repeat
    end tell
    return outText
end run
'''
ACCEPT_SUBJ = re.compile(r"^(Accepted|Declined|Tentative|Canceled|Cancelled)\s*:", re.I)
REPLY_SUBJ = re.compile(r"^\s*(re|aw|sv)\s*:", re.I)
FWD_SUBJ = re.compile(r"^\s*(fw|fwd)\s*:", re.I)


def profile_dir():
    return os.path.expanduser(PROFILE)


def available():
    if sys.platform != "darwin":
        return False, "Outlook for Mac extraction is macOS-only"
    if not os.path.exists(os.path.join(profile_dir(), "Outlook.sqlite")):
        return False, "legacy Outlook for Mac profile not found (New Outlook has no local store)"
    if not shutil.which("osascript"):
        return False, "osascript not available"
    return True, "ok"


def outlook_running():
    """True if Outlook is already running. Never launches it: `application ... is running` does not
    start the app, unlike `tell application`, which would auto-launch it on every batch."""
    try:
        res = subprocess.run(["osascript", "-e", 'application "Microsoft Outlook" is running'],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0 and res.stdout.strip() == "true"


def _own_addresses(con):
    own = set()
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    for t in tables:
        if not t.lower().startswith("accounts"):
            continue
        cols = [r[1] for r in con.execute("pragma table_info(%s)" % t)]
        for c in cols:
            if c.lower() in ("account_emailaddress", "account_email"):
                for (v,) in con.execute("select %s from %s where %s is not null" % (c, t, c)):
                    if v and "@" in v:
                        own.add(v.strip().lower())
    return own


def split_list(s):
    return [x.strip() for x in (s or "").split(";") if x.strip()]


def extract(addresses=(), names=(), stats=None, batch=50):
    """addresses: extra own addresses. names: what the person signs with (for sign-off detection)."""
    stats = stats if stats is not None else {}
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    tmp = tempfile.mkdtemp(prefix="wlm_olk_")
    try:
        for suf in ("", "-wal", "-shm"):
            src = os.path.join(profile_dir(), "Outlook.sqlite" + suf)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(tmp, "Outlook.sqlite" + suf))
        con = sqlite3.connect("file:%s?mode=ro" % os.path.join(tmp, "Outlook.sqlite"), uri=True)
        own = _own_addresses(con) | {a.strip().lower() for a in addresses if a}
        fcols = {r[1] for r in con.execute("pragma table_info(Folders)")}
        ftype = "Folder_SpecialFolderType" if "Folder_SpecialFolderType" in fcols else "0"
        folders, skip_fids = {}, set()
        for fid, fname, ft in con.execute("select Record_RecordID, Folder_Name, %s from Folders" % ftype):
            folders[fid] = fname
            if ft in SKIP_SPECIAL_TYPES or fname in SKIP_FOLDERS:
                skip_fids.add(fid)
        rows = con.execute(
            "select Record_RecordID, Record_FolderID, Message_TimeSent, Message_SenderAddressList,"
            " Message_ToRecipientAddressList, Message_CCRecipientAddressList, Message_DisplayTo,"
            " Message_RecipientList, Message_NormalizedSubject from Mail").fetchall()
        con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not own:
        raise RuntimeError("could not find your own address; pass --address you@example.com")
    # A draft (even in a renamed folder) has no sent time; skip those too.
    cand = [r for r in rows if r[1] not in skip_fids and r[2]
            and (r[3] or "").strip().lower() in own]
    stats["own_addresses"] = len(own)
    stats["candidates"] = len(cand)

    fetched = {}
    ids = [str(r[0]) for r in cand]
    if ids and not outlook_running():
        # Fail once instead of letting each AppleScript batch auto-launch Outlook again.
        raise RuntimeError("Outlook is not running. Open Microsoft Outlook, wait for it to finish loading, "
                           "and retry (this command never launches it).")
    for i in range(0, len(ids), batch):
        if not outlook_running():
            raise RuntimeError("Outlook quit during extraction. Open Outlook and retry.")
        try:
            res = subprocess.run(["osascript", "-e", APPLESCRIPT] + ids[i:i + batch],
                                 capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise RuntimeError("AppleScript timed out talking to Outlook. Open Outlook and retry.")
        if res.returncode != 0:
            raise RuntimeError("AppleScript failed (open Outlook, allow Automation for your terminal, and retry): "
                               + res.stderr.strip()[:300])
        got = 0
        for rec in res.stdout.split(RS):
            parts = rec.lstrip("\n").split(FS, 3)
            if len(parts) == 4:
                fetched[parts[0]] = parts[1:]
                got += parts[2] != "error"
        if i == 0 and got == 0:
            raise RuntimeError("Outlook returned no messages for the first batch (still loading, or "
                               "Automation denied). Open Outlook, let it finish loading, and retry.")

    out = []
    for r in sorted(cand, key=lambda x: x[2] or 0):
        rid, fid, ts, sender, to, cc, disp, rlist, nsubj = r
        subj, is_mtg, body = fetched.get(str(rid), ("", "error", ""))
        if is_mtg == "error":
            stats["fetch_errors"] = stats.get("fetch_errors", 0) + 1
            continue
        rec = email_record("outlook", "outlook:%s" % rid, subj or nsubj or "", body,
                           datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else "",
                           split_list(rlist) or split_list(disp) or split_list(to), names)
        if rec:
            out.append(rec)
    stats["written"] = len(out)
    return out


def email_record(source, rid, subject, body, timestamp, recipients, names=()):
    """Shared by Outlook and .eml/.mbox: clean one sent email into a record (or None)."""
    authored, quoted = split_parent(body or "")
    c = clean_email_body(authored, names)
    text = c["text"]
    if not text or (ACCEPT_SUBJ.match(subject or "") and len(text.split()) < 3):
        return None
    flags = set()
    if FWD_SUBJ.match(subject or ""):
        flags.add("forward")
    thread = re.sub(r"^\s*((re|aw|sv|fw|fwd)\s*:\s*)+", "", subject or "", flags=re.I).strip()
    return make_record(source, "email", text, rid=rid, timestamp=timestamp,
                       recipient="; ".join(recipients[:3]), thread=thread,
                       context=parent_excerpt(quoted), flags=flags, subject=subject or "",
                       greeting=c["greeting"], signoff=c["signoff"],
                       is_reply=bool(REPLY_SUBJ.match(subject or "")), to_count=len(recipients))
