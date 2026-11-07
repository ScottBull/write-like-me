"""A folder (or single file) of the person's writing: .txt / .md / .eml / .mbox.

- .eml / .mbox: kept only when From: is one of the person's addresses (--address), or, with
  all_mine=True, when the person confirms every message there is mail they sent (e.g. a Sent
  folder export). Quotes and signatures are removed. Register "email".
- .txt / .md: treated as documents the person wrote (register "doc" unless overridden), split into
  paragraph chunks of up to ~180 words. Code blocks, "> " quoted blocks and front matter are dropped.

Works on any OS (stdlib email / mailbox only).
"""
import email
import email.policy
import email.utils
import mailbox
import os
import re
from datetime import timezone

from ..clean import html_to_text, paste_flags
from ..common import make_record, short_hash
from .outlook_mac import email_record

TEXT_EXT = (".txt", ".md", ".markdown")
MAIL_EXT = (".eml",)
MBOX_EXT = (".mbox", ".mbx")
CHUNK_WORDS = 180


def iter_files(path):
    if os.path.isfile(path):
        yield path
        return
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in sorted(files):
            if f.startswith("."):
                continue
            p = os.path.join(root, f)
            if f.lower().endswith(TEXT_EXT + MAIL_EXT + MBOX_EXT) or os.path.basename(root).lower().endswith(".mbox"):
                yield p


def _body_text(msg):
    """Best plain-text body of an email.message.Message."""
    if hasattr(msg, "get_body"):
        # the sender's own body only: get_body() never descends into attached messages
        # (message/rfc822) or attachments, which walk() would
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is None:
            return ""
        try:
            payload = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            payload = payload.decode(part.get_content_charset() or "utf-8", "replace")
        return html_to_text(payload or "") if part.get_content_type() == "text/html" else (payload or "")
    if msg.is_multipart():
        plain = html = None
        for part in msg.walk():
            if part.get_content_maintype() == "multipart" or part.get_filename():
                continue
            ct = part.get_content_type()
            try:
                payload = part.get_content() if hasattr(part, "get_content") else part.get_payload(decode=True)
            except Exception:
                payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                payload = payload.decode(part.get_content_charset() or "utf-8", "replace")
            if ct == "text/plain" and plain is None:
                plain = payload
            elif ct == "text/html" and html is None:
                html = payload
        if plain:
            return plain
        return html_to_text(html) if html else ""
    try:
        payload = msg.get_content() if hasattr(msg, "get_content") else msg.get_payload(decode=True)
    except Exception:
        payload = msg.get_payload(decode=True)
    if isinstance(payload, bytes):
        payload = payload.decode(msg.get_content_charset() or "utf-8", "replace")
    if msg.get_content_type() == "text/html":
        return html_to_text(payload or "")
    return payload or ""


def mail_to_record(msg, own, all_mine, names, src_id):
    frm = email.utils.parseaddr(str(msg.get("From", "")))[1].lower()
    if not all_mine and frm not in own:
        return None
    subj = str(msg.get("Subject", "") or "")
    ts = ""
    try:
        d = email.utils.parsedate_to_datetime(str(msg.get("Date")))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        ts = d.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    rcpts = [n or a for n, a in email.utils.getaddresses([str(msg.get("To", "")), str(msg.get("Cc", ""))]) if a]
    return email_record("email_file", "eml:" + src_id, subj, _body_text(msg), ts, rcpts, names)


def split_long(para, max_words=CHUNK_WORDS):
    """Split a paragraph over max_words at sentence boundaries (a single sentence that is still too
    long is split by words), so dense documents aren't dropped by the bank's length cap."""
    if len(para.split()) <= max_words:
        return [para]
    out, cur, n = [], [], 0
    for sent in re.split(r"(?<=[.!?…])\s+", para):
        w = sent.split()
        while len(w) > max_words:
            if cur:
                out.append(" ".join(cur))
                cur, n = [], 0
            out.append(" ".join(w[:max_words]))
            w = w[max_words:]
        if not w:
            continue
        if cur and n + len(w) > max_words:
            out.append(" ".join(cur))
            cur, n = [], 0
        cur.append(" ".join(w))
        n += len(w)
    if cur:
        out.append(" ".join(cur))
    return out


def chunk_doc(text, max_words=CHUNK_WORDS):
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)       # front matter
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"(?m)^[ \t]*>.*\n?", "", text)                 # quoted blocks: someone else's words
    paras = []
    for p in (p.strip() for p in re.split(r"\n\s*\n", text)):
        if p:
            paras += split_long(p, max_words)
    chunks, cur, n = [], [], 0
    for p in paras:
        if re.match(r"^#{1,6} ", p) and not cur:
            continue
        w = len(p.split())
        if cur and n + w > max_words:
            chunks.append("\n\n".join(cur))
            cur, n = [], 0
        cur.append(p)
        n += w
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def extract(path, addresses=(), all_mine=False, names=(), doc_register="doc", stats=None):
    stats = stats if stats is not None else {}
    own = {a.strip().lower() for a in addresses if a}
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        raise RuntimeError("no such file or folder: %s" % path)
    out, skipped_not_mine = [], 0
    for p in iter_files(path):
        low = p.lower()
        base = os.path.basename(p)
        rel = os.path.relpath(p, path) if os.path.isdir(path) else base   # unique within the folder
        try:
            if low.endswith(MAIL_EXT):
                with open(p, "rb") as f:
                    msg = email.message_from_binary_file(f, policy=email.policy.default)
                r = mail_to_record(msg, own, all_mine, names, short_hash(rel, msg.get("Message-ID", "")))
                if r:
                    out.append(r)
                else:
                    skipped_not_mine += 1
            elif low.endswith(MBOX_EXT) or os.path.basename(os.path.dirname(p)).lower().endswith(".mbox"):
                if not low.endswith(MBOX_EXT) and base != "mbox":
                    continue
                mb = mailbox.mbox(p, factory=lambda f: email.message_from_binary_file(f, policy=email.policy.default),
                                  create=False)
                try:
                    for i, msg in enumerate(mb):
                        r = mail_to_record(msg, own, all_mine, names, short_hash(rel, i, msg.get("Message-ID", "")))
                        if r:
                            out.append(r)
                        else:
                            skipped_not_mine += 1
                finally:
                    mb.close()
            elif low.endswith(TEXT_EXT):
                with open(p, encoding="utf-8", errors="replace") as f:
                    txt = f.read()
                for k, ch in enumerate(chunk_doc(txt)):
                    out.append(make_record("file", doc_register, ch, rid="file:%s:%d" % (short_hash(p), k),
                                           thread=base, flags=paste_flags(ch)))
        except Exception as e:  # a broken file shouldn't stop the folder
            stats.setdefault("errors", []).append("%s: %s" % (base, type(e).__name__))
    stats["written"] = len(out)
    stats["skipped_not_from_you"] = skipped_not_mine
    return out
