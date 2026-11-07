"""Samples the person pastes in. One file, samples separated by a line containing only `---`.

Optional per-sample header lines (removed from the text):
    to: <recipient>
    context: <the message you were replying to>
"""
import re

from ..clean import paste_flags
from ..common import make_record, now_iso

SEP = re.compile(r"(?m)^\s*---\s*$")


def parse(text, register, recipient=""):
    out = []
    for k, chunk in enumerate(SEP.split(text or "")):
        lines = chunk.strip("\n").split("\n")
        rcpt, ctx = recipient, ""
        while lines and re.match(r"^(to|context):\s", lines[0], re.I):
            key, val = lines.pop(0).split(":", 1)
            if key.lower() == "to":
                rcpt = val.strip()
            else:
                ctx = val.strip()
        body = "\n".join(lines).strip()
        if body:
            out.append(make_record("pasted", register, body, recipient=rcpt, context=ctx,
                                   thread=rcpt, flags=paste_flags(body), added=now_iso()))
    return out
