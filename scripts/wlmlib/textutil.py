"""Text normalisation, tokenising and PII masking used by stats, lint and retrieval."""
import re

RE_FENCE = re.compile(r"```.*?```", re.S)
RE_INLINE = re.compile(r"`[^`\n]+`")
RE_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
RE_PHONE = re.compile(r"(?<![\w])(?:\+?\d{1,3}[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}(?![\w])")
# international / national formats ("+44 20 7946 0958", "07700 900123", "+49 (0)30 123456"): 9-15 digits
RE_PHONE_INTL = re.compile(r"(?<![\w+])\+?\(?\d[\d ().-]{7,20}\d(?![\w])")
RE_PATH = re.compile(
    r"(?<![\w])(?:~|\.{1,2})?/?(?:[\w.@-]+/){2,}[\w.@-]*"
    r"|\b[\w-]+\.(?:md|py|js|ts|tsx|jsx|json|html|css|txt|pdf|pptx?|docx?|xlsx?|csv|png|jpe?g|svg"
    r"|mp4|mov|sh|ya?ml|swift|zip|env)\b", re.I)
RE_QUOTE = re.compile(r"[\"“]([^\"”\n]{10,})[\"”]")
QUOTE_MIN_WORDS = 8
PLACE = {"CODE", "URL", "EMAIL", "PATH", "QUOTE"}
RE_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
RE_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")
RE_EMOTICON = re.compile(r"(?:^|\s)(?:[:;=][-']?[)(DPp/\\|]|<3|\^\^|xD)(?=\s|$|[.,!?])")


def normalise(t):
    """Replace code, URLs, emails, paths and long quotations with placeholder tokens so
    style counts reflect the person's own prose."""
    t = RE_FENCE.sub(" ", t or "")
    t = RE_INLINE.sub(" CODE ", t)
    t = RE_URL.sub(" URL ", t)
    t = RE_EMAIL.sub(" EMAIL ", t)
    t = RE_PATH.sub(" PATH ", t)
    t = RE_QUOTE.sub(lambda m: " QUOTE " if len(m.group(1).split()) >= QUOTE_MIN_WORDS else m.group(0), t)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def words(t):
    return [w.lower().replace("’", "'") for w in RE_WORD.findall(t or "") if w not in PLACE]


def raw_words(t):
    return [w.replace("’", "'") for w in RE_WORD.findall(t or "") if w not in PLACE]


def sentences(t):
    parts = []
    for line in (t or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        parts += [p for p in re.split(r"(?<=[.!?…])\s+", line) if p.strip()]
    return [p for p in parts if words(p)]


def first_letter(s):
    s = re.sub(r"^(?:[-*•>\d.)\s\"'“(]|CODE|URL|PATH|QUOTE|EMAIL)+", "", s or "")
    m = re.search(r"[A-Za-z]", s)
    return s[m.start()] if m and m.start() == 0 else None


def word_count(t):
    return len(words(normalise(t)))


def _mask_phones(t):
    t = RE_PHONE.sub("[phone]", t)

    def intl(m):
        n = sum(c.isdigit() for c in m.group(0))
        return "[phone]" if 9 <= n <= 15 else m.group(0)
    return RE_PHONE_INTL.sub(intl, t)


def mask_pii(t):
    """Mask email addresses and phone numbers. Applied to the exemplar bank by default."""
    t = RE_EMAIL.sub("[email]", t or "")
    return _mask_phones(t)


def mask_recipient(r):
    """A recipient field can be a bare address ("dana.lee@example.com") or "Name <address>".
    Keep the display name, reduce a bare address to its local part plus the organisation part of
    the domain ("orders@millstoneflour.com" -> "orders (millstoneflour)"; free-mail domains are
    dropped), and mask phone numbers, so no address reaches a prompt while recipient matching
    ("Dana", "Millstone Flour") still works."""
    r = re.sub(r"\s*<[^<>@\s]+@[^<>\s]+>", "", r or "")
    r = RE_EMAIL.sub(_reduce_address, r)
    return _mask_phones(r).strip()


FREEMAIL = {"gmail", "googlemail", "outlook", "hotmail", "live", "msn", "yahoo", "ymail", "icloud", "me",
            "mac", "aol", "proton", "protonmail", "pm", "gmx", "mail", "fastmail", "zoho", "yandex", "hey"}


def _reduce_address(m):
    local, domain = m.group(0).split("@", 1)
    parts = domain.lower().split(".")[:-1]  # drop the TLD
    if len(parts) > 1 and parts[-1] in ("co", "com", "org", "net", "ac", "gov", "edu", "ltd", "plc"):
        parts = parts[:-1]  # example.co.uk
    org = parts[-1] if parts else ""
    return "%s (%s)" % (local, org) if org and org not in FREEMAIL else local


def toks(s):
    return re.findall(r"[a-z0-9']+", (s or "").lower())


def ngrams(t, n=6):
    w = toks(t)
    return {tuple(w[k:k + n]) for k in range(len(w) - n + 1)}
