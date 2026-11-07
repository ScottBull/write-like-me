"""Cleaning shared by the extractors: keep only the words the person wrote.

- split_parent(): cut quoted reply chains / forwarded content out of an email body
- clean_email_body(): drop disclaimers, meeting boilerplate and signature blocks
- html_to_text(): HTML (chat apps, email) to plain text, skipping quoted replies
- paste_flags(): heuristics for pasted code / relayed AI text
"""
import html
import re
from html.parser import HTMLParser

QUOTE_START = [
    re.compile(r"^_{8,}\s*$"),                                   # Outlook reply separator
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.I),
    re.compile(r"^-{2,}\s*Forwarded message\s*-{2,}", re.I),
    re.compile(r"^Begin forwarded message:", re.I),
    re.compile(r"^On .{5,200}wrote:\s*$", re.I),
    re.compile(r"^From:\s.+", re.I),
    re.compile(r"^>"),
]
HEADER_LINE = re.compile(r"^(From|Sent|Date|To|Cc|Subject):", re.I)
MEETING = re.compile(r"(Microsoft Teams (meeting|Need help)|Join the meeting now|Meeting ID:|"
                     r"Join on your computer|_{20,}|Dial in by phone|Find a local number|"
                     r"Join Zoom Meeting|zoom\.us/j/|meet\.google\.com/)", re.I)
DISCLAIMER = re.compile(r"(This (e-?mail|communication|message).{0,80}(confidential|privileged|"
                        r"intended (only|solely))|CONFIDENTIALITY NOTICE|"
                        r"If you are not the intended recipient)", re.I)
SIG_MARK = re.compile(r"^(--\s*$|-- |Sent from my (iPhone|iPad|Android|mobile)|Get Outlook for (iOS|Android)|"
                      r"Sent from (Mail|Outlook|Yahoo Mail))", re.I)
CONTACT_LINE = re.compile(
    r"(^\s*(?:[MmOoTtPpCcWw](?:obile|ffice|el|hone|ell|ork)?)?\s*[:.]?\s*\+?[\d(][\d ().-]{7,}\s*$"
    r"|^\s*\+?1?[ .(-]*\d{3}[ ).-]*\d{3}[ .-]*\d{4}\s*$"
    r"|^\s*(?:https?://|www\.)\S+\s*$"
    r"|^\s*[\w.+-]+@[\w-]+\.[\w.]+\s*$"
    r"|\s\|\s"
    r"|^\s*\((?:she|he|they)/\w+\)\s*$)", re.I)
SIGNOFF = re.compile(r"^(thanks|thank you|thx|cheers|best|regards|best regards|kind regards|"
                     r"warm regards|many thanks|thanks again|thanks so much|thanks all|all the best|"
                     r"appreciate it|talk soon|take care|sincerely|yours|ta)\b[^\n]{0,25}[,!.]?\s*$", re.I)
GREETING = re.compile(r"^(hi|hey|hello|dear|good (morning|afternoon|evening)|morning|"
                      r"all|team|folks|y'?all|greetings)\b[^.!?\n]{0,40}[,!:.\-]?\s*$"
                      r"|^[A-Z][a-z]+( and [A-Z][a-z]+| ?[&+/] ?[A-Z][a-z]+)?\s*[,:\-–—]\s*$", re.I)
GREETING_INLINE = re.compile(r"^((?:hi|hey|hello|dear|good (?:morning|afternoon))\b[^,!\n]{0,30}[,!])\s+(?=\S)",
                             re.I)
URL_ONLY = re.compile(r"^\s*(?:https?://|www\.)\S+\s*$", re.I)
CID = re.compile(r"\[cid:[^\]]*\]|<mailto:[^>]*>|<https?://[^>]*>|\s?<OWAAutoLink>")


def split_parent(body):
    """Return (authored_part, quoted_part)."""
    lines = (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        hit = any(p.match(s) for p in QUOTE_START)
        if hit and s.lower().startswith("from:"):
            # require a following header line so prose like "From: my side..." survives
            nxt = [x.strip() for x in lines[i + 1:i + 5] if x.strip()]
            hit = any(HEADER_LINE.match(x) for x in nxt)
        if hit:
            return "\n".join(lines[:i]), "\n".join(lines[i:])
    return "\n".join(lines), ""


def parent_excerpt(quoted, n=300):
    """First authored lines of the quoted message (the thing being replied to)."""
    if not quoted:
        return ""
    out, in_hdr = [], True
    for ln in quoted.split("\n"):
        s = ln.strip()
        if in_hdr:
            if (not s or QUOTE_START[0].match(s) or HEADER_LINE.match(s) or s.startswith("-----")
                    or (s.lower().startswith("on ") and s.endswith("wrote:"))):
                continue
            in_hdr = False
        if QUOTE_START[0].match(s) or (HEADER_LINE.match(s) and s.lower().startswith("from:")):
            break
        if (s.lower().startswith("on ") and s.endswith("wrote:")):
            break
        out.append(s.lstrip("> ").strip())
    txt = re.sub(r"\s+", " ", CID.sub("", " ".join(out))).strip()
    return txt[:n]


def _name_line_re(names):
    alts = [re.escape(n.strip()) for n in (names or []) if n and n.strip()]
    if not alts:
        return None
    return re.compile(r"^-?\s*(?:%s)\.?\s*$" % "|".join(alts), re.I)


def _prose_line(s):
    """A line that reads like message text rather than a signature line."""
    n = len(s.split())
    return (n >= 5 and s.endswith((".", "?", "!"))) or n >= 10


def clean_email_body(text, own_names=None):
    """Return dict(text, greeting, signoff). own_names: first name / initials / full name the
    person signs with; used to recognise the sign-off and cut the signature block below it."""
    text = CID.sub("", html.unescape(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("​", "")
    lines = [ln.rstrip() for ln in text.split("\n")]
    for i, ln in enumerate(lines):
        if MEETING.search(ln) or DISCLAIMER.search(ln) or SIG_MARK.match(ln.strip()):
            lines = lines[:i]
            break
    name_re = _name_line_re(own_names)
    nonblank_idx = [i for i, ln in enumerate(lines) if ln.strip()]
    # signature block: a sign-off line in the last 10 non-blank lines, optionally followed by
    # a name line; everything after that is signature (title, phone, company...).
    cut = None
    for i in reversed(nonblank_idx[-10:]):
        s = lines[i].strip()
        if SIGNOFF.match(s) and i != (nonblank_idx[0] if nonblank_idx else -1):
            after = [j for j in nonblank_idx if j > i]
            keep_to = i
            if after:
                nxt = lines[after[0]].strip()
                if (name_re and name_re.match(nxt)) or (len(nxt.split()) <= 3 and len(nxt) <= 30
                                                        and not nxt.endswith((".", "?", "!"))
                                                        and re.match(r"^[-~]?\s*[A-Z]", nxt)):
                    keep_to = after[0]
            # a sign-off is terminal only if what follows reads like a signature block (name,
            # title, phone...), not more paragraphs: "Thanks for taking a look." mid-email
            if not any(_prose_line(lines[j].strip()) for j in nonblank_idx if j > keep_to):
                cut = keep_to
            break
    if cut is not None:
        lines = lines[:cut + 1]
    else:
        while lines and (not lines[-1].strip() or CONTACT_LINE.search(lines[-1])):
            if URL_ONLY.match(lines[-1]):
                # a bare trailing URL is usually content ("Link:\nhttps://..."); drop it only when
                # it sits under other contact lines (phone / email / "a | b"), i.e. a signature block
                prev = [ln for ln in lines[:-1] if ln.strip()]
                if not prev or not CONTACT_LINE.search(prev[-1]) or URL_ONLY.match(prev[-1]):
                    break
            lines.pop()
        if name_re:
            nb = [i for i, ln in enumerate(lines) if ln.strip()]
            for k in nb[-6:]:
                if name_re.match(lines[k].strip()) and k != nb[0]:
                    lines = lines[:k + 1]
                    break
    out, blank = [], False
    for ln in lines:
        if not ln.strip():
            if not blank and out:
                out.append("")
            blank = True
        else:
            out.append(ln)
            blank = False
    while out and not out[-1].strip():
        out.pop()
    body = "\n".join(out).strip()
    nonblank = [ln.strip() for ln in out if ln.strip()]
    greeting = ""
    if nonblank:
        first = nonblank[0]
        if GREETING.match(first) and (len(nonblank) > 1 or re.match(r"(?i)^(hi|hey|hello|dear)\b", first)) \
                and not SIGNOFF.match(first):
            greeting = first
        else:
            m = GREETING_INLINE.match(first)
            if m:
                greeting = m.group(1).strip()
    signoff = ""
    if len(nonblank) > 1:
        parts = []
        for s in reversed(nonblank[-2:]):
            if (name_re and name_re.match(s)) or SIGNOFF.match(s):
                parts.insert(0, s)
                if SIGNOFF.match(s):
                    break
            elif parts == [] and len(s.split()) <= 2 and len(nonblank) > 2 and SIGNOFF.match(nonblank[-2]):
                parts.insert(0, s)
            else:
                break
        if parts and parts[-1] != greeting:
            signoff = " / ".join(parts)
    return {"text": body, "greeting": greeting, "signoff": signoff}


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "pre", "ul", "ol", "table"}
    SKIP_ITEMTYPES = ("schema.skype.com/Reply",)
    SKIP_CLASSES = ("gmail_quote", "moz-cite-prefix", "yahoo_quoted", "OutlookMessageHeader")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip_depth = [], 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        itemtype = a.get("itemtype") or ""
        cls = a.get("class") or ""
        if self.skip_depth:
            if tag not in ("br", "img", "hr", "meta", "input"):
                self.skip_depth += 1
            return
        if tag in ("blockquote", "style", "script", "head") or any(s in itemtype for s in self.SKIP_ITEMTYPES) \
                or any(c in cls for c in self.SKIP_CLASSES):
            self.skip_depth = 1
            return
        if tag == "img":
            alt = a.get("alt") or ""
            if "Emoji" in itemtype or (alt and len(alt) <= 8 and not alt.isascii()):
                self.out.append(alt)
            return
        if tag == "br":
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if self.skip_depth:
            if tag not in ("br", "img", "hr", "meta", "input"):
                self.skip_depth -= 1
            return
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.out.append(data)

    def text(self):
        t = "".join(self.out).replace("\r", "").replace("\xa0", " ")
        t = re.sub(r"[ \t]+\n", "\n", t)
        t = re.sub(r"\n[ \t]+", "\n", t)
        t = re.sub(r"[ \t]{2,}", " ", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()


def html_to_text(content):
    p = _HTMLText()
    try:
        p.feed(content or "")
        p.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", content or "").strip()
    return p.text()


CODE_LINE_RE = re.compile(
    r"^\s*(?:[{}\[\]();<>$#|>]|at\s|\w+Error\b|Traceback|File \"|\d{2}:\d{2}:\d{2}|"
    r"[\w.-]+\s*[:=]\s*[\"'{\[\d]|import\s|from\s\S+\simport|def\s|class\s|"
    r"function\s|const\s|let\s|var\s|return\s|\s{4,}\S)")


def paste_flags(text):
    """Heuristic flags for text that is probably not the person's own prose."""
    flags = set()
    lines = [l for l in (text or "").splitlines() if l.strip()]
    if "```" in (text or ""):
        flags.add("possible_inline_paste")
    elif len(lines) >= 4:
        code = sum(1 for l in lines if CODE_LINE_RE.match(l))
        if code / len(lines) >= 0.5:
            flags.add("possible_inline_paste")
    md = len(re.findall(r"(?m)^#{1,4} \S", text or "")) + len(re.findall(r"\*\*[^*\n]+\*\*", text or ""))
    if md >= 3 or (len(re.findall(r"(?m)^\s*[-*] \S", text or "")) >= 5 and len(text or "") > 800):
        flags.add("possible_relayed_ai_text")
    return flags
