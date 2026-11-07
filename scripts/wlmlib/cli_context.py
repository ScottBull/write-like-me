"""`wlm context`: select archive entries within the budget and write the draft's context files.

    wlm context --register R [--budget N] [--strategy D]
                [--notes ...|--notes-file F|--restyle-file F [--keep mine|all] [--point TEXT]]
                [--context ...|--context-file F] [--recipient X] [--subject S]
                [--person core|full|none] [--habits on|off] [--seed N]

Writes <pack>/work/ctx-<hash>/system.txt (the system prompt file: preface, optional person block,
the selected entries in date order, the framing), task.txt (the new entry with the notes) and
context.json (register, strategy, budget, counts and the selected ids; no message text), all 0600,
and prints the paths and counts. The voice-writer subagent reads system.txt + task.txt, and
`wlm draft` sends them (system.txt as --system-prompt-file, task.txt on stdin).

The budget covers the archive entries, the person block, the learned notes and the habits block
together. Defaults come from stats.json (`budget_default`, `strategy_default`, `person_default`,
`habits_default`; build.py sets the first two, and the others fall back to PERSON_DEFAULT /
HABITS_DEFAULT there).

Habits block (habits.py): when on, a few measured habits of the archive's register from stats.json
(length, open endings, acknowledgment openers, "!" and hedges, never-used marks already in rules.md),
phrased as frequencies over many messages, go after the learned notes under "How my messages vary".
It is dropped, with a note, when it does not fit the budget beside the learned notes.

Restyle (--restyle-file): the notes are built from pasted text (usually an answer Claude already
wrote) with the wording measured in the restyle test (docs/research.md, "Restyle"). `--keep mine`,
the default, is R-SHORT: a line saying who the reply is to and where (register, recipient, the
archive's platform), then "Below is an AI-written answer with more detail than I would send. ...".
`--keep all` is R-DIRECT: "Rewrite this in my own words, ... Keep the useful content." `--point`
puts "The point I want to make: TEXT" before the pasted text. Restyles were spotted more often than
drafts from the user's own notes, so the command always prints a one-line tip saying so.

Learned notes: the blocks of <pack>/learned.md that apply to this register and recipient
(compose.learned_for, what `wlm learn note` writes) go after the archive as a short "notes from my
edits" block, without their headings. learned.md's own explanation is never sent.

Person file contract (built by person.py, or written by hand): <pack>/person.md (personal) and
<pack>/person.work.md (work), plain markdown, citation markers already stripped. person.py writes
`<!-- tier: core -->` before the core and `<!-- tier: full -->` before the extension. Everything above
the full marker is the core tier; the whole file is the full tier; a file without it is all core.
The marker is matched with or without the space (`<!-- tier:full -->`, the research spelling) and in
any case; other markers such as `<!-- tier: core -->` or `<!-- /tier:core -->` are plain comments.
HTML comments are never sent. Both files are used when present, unless stats.json `person_files`
lists which.
"""
import datetime as dt
import os
import re
import shutil
import sys

from . import build, compose
from . import habits as H
from . import select as S
from .archive import archive_path, has_archive
from .build import HABITS_CHOICES, PERSON_DEFAULT, defaults, habits_default
from .common import REGISTERS, pack_dir, private_open, read_json, read_jsonl, short_hash, write_json

PERSON_FILES = ("person.md", "person.work.md")
TIER_MARK = re.compile(r"<!--\s*tier\s*:\s*full\s*-->", re.I)   # not "<!-- /tier:full -->"
COMMENT = re.compile(r"<!--.*?-->", re.S)
KEEP_DIRS = 30
# Where to look when the register has no archive (the old `kit` fallback order).
FALLBACK = {"chat": ["email", "post", "doc", "prompt"], "email": ["chat", "post", "doc", "prompt"],
            "doc": ["post", "email", "chat", "prompt"], "prompt": ["chat", "email", "post", "doc"],
            "post": ["doc", "chat", "email", "prompt"]}


# Restyle wordings, verbatim from the restyle test (R-SHORT for "mine", R-DIRECT for "all").
RESTYLE_SHORT = ("Below is an AI-written answer with more detail than I would send. Reply the way I actually would: "
                 "as long as I would really write it, keeping only what I would bother to mention. "
                 "Don't add anything that isn't in it.")
RESTYLE_ALL = "Rewrite this in my own words, the way I would write it myself. Keep the useful content."
RESTYLE_TIP = ("Tip: drafts from your own one-line point sound most like you; "
               "add --point '<what you want to say>' or use --notes.")
KEEP_CHOICES = ("mine", "all")
# An archive whose entries all come from one of these sources names the platform in the framing.
PLATFORMS = {"teams": "Teams", "imessage": "iMessage", "reddit": "Reddit"}


def _platform(pack, register):
    """The platform name when every archive entry of the register comes from one known source, else ''."""
    srcs = {e.get("source") for e in read_jsonl(archive_path(pack, register))}
    return PLATFORMS.get(srcs.pop(), "") if len(srcs) == 1 else ""


def restyle_where(register, recipient="", subject="", platform=""):
    """The R-SHORT opening line: who the reply goes to and where ("This is a Teams chat reply to Dana.")."""
    to = recipient.strip()
    if register == "chat":
        kind = (platform + " chat") if platform else "chat"
        return "This is a %s reply to %s." % (kind, to or ("a coworker" if platform == "Teams" else "someone"))
    if register == "post":
        kind = (platform + " reply") if platform else "reply to a public post"
        return "This is a %s%s." % (kind, (" in " + to) if to else "")
    if register == "email":
        return "This is an email to %s%s." % (to or "someone", (" about " + subject.strip()) if subject.strip() else "")
    if register == "doc":
        return "This is a document%s." % ((" for " + to) if to else "")
    return "This is a prompt to an AI assistant."


def restyle_notes(text, register, keep="mine", recipient="", subject="", point="", platform=""):
    """The draft's notes for restyling `text`: the measured framing, the optional point, then the text."""
    if keep not in KEEP_CHOICES:
        raise ValueError("unknown --keep %r" % keep)
    head = RESTYLE_ALL if keep == "all" else restyle_where(register, recipient, subject, platform) + " " + RESTYLE_SHORT
    parts = [head]
    if (point or "").strip():
        parts.append("The point I want to make: " + point.strip())
    parts.append(text.strip())
    return "\n\n".join(parts)


def _read(path_or_none, text_or_none):
    if path_or_none:
        with open(os.path.expanduser(path_or_none), encoding="utf-8") as f:
            return f.read()
    return text_or_none or ""


def person_tier(text, tier):
    """The `core` or `full` tier of one person file's text ('' for `none`): core is everything
    before the first full-tier marker, full is the whole file, no marker means all core."""
    if tier == "none" or not (text or "").strip():
        return ""
    parts = TIER_MARK.split(text, 1)
    body = parts[0] if tier == "core" else "\n".join(parts)
    return re.sub(r"\n{3,}", "\n\n", COMMENT.sub("", body)).strip()


def load_person(pack, tier, stats=None):
    """(text, [file names used]) for the tier, from the pack's person files that exist."""
    if tier == "none":
        return "", []
    names = (stats or {}).get("person_files") or PERSON_FILES
    out, used = [], []
    for n in names:
        p = os.path.join(pack, os.path.basename(n))
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8") as f:
            t = person_tier(f.read(), tier)
        if t:
            out.append(t)
            used.append(os.path.basename(n))
    return "\n\n".join(out), used


def load_learned(pack, register, recipient=""):
    """The learned.md notes for this register and recipient, as the context shows them ('' for none)."""
    p = os.path.join(pack, "learned.md")
    if not os.path.isfile(p):
        return ""
    with open(p, encoding="utf-8") as f:
        return compose.learned_notes(f.read(), register, recipient)


def fit_budget(budget, person_text, learned_text):
    """(person_text, note): the budget covers the person file and the learned notes too, so when the two
    together are over it the person file is left out (note says so). Learned notes alone over the
    budget raise ValueError: they are the user's own instructions and are never dropped silently."""
    l_tok = S.learned_tokens(learned_text)
    if l_tok > budget:
        raise ValueError("the learned notes for this draft alone are about %d tokens, over the budget of %d; "
                         "raise --budget (or budget_default in stats.json) or shorten learned.md" % (l_tok, budget))
    p_tok = S.person_tokens(person_text)
    if person_text and p_tok + l_tok > budget:
        return "", ("note: the person file (%d tokens) does not fit the budget of %d with the learned notes; "
                    "it was left out (use --person core or a bigger --budget)" % (p_tok, budget))
    return person_text, ""


def fit_habits(budget, learned_text, habits_text):
    """(habits_text, note): the habits block is left out (note says so) when it and the learned notes
    together are over the budget; the notes come first."""
    h_tok = S.habits_tokens(habits_text)
    if h_tok and S.learned_tokens(learned_text) + h_tok > budget:
        return "", ("note: the habits block (%d tokens) does not fit the budget of %d with the learned notes; "
                    "it was left out" % (h_tok, budget))
    return habits_text, ""


def load_rules(pack):
    """rules.md's text, or None when the pack has none (habits.hard_marks then reads stats.json)."""
    p = os.path.join(pack, "rules.md")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()


def archive_register(pack, register):
    """(register to use, note or ''): the register itself if it has an archive, else the first in the
    fallback order that does, else (None, reason)."""
    def has(r):
        return bool(read_jsonl(archive_path(pack, r)))
    if has(register):
        return register, ""
    alt = next((r for r in FALLBACK[register] if has(r)), None)
    if not alt:
        return None, "the pack has no archive entries at all; run voice-setup (or `wlm build`)"
    return alt, ("note: no %s samples in the pack; using %s samples instead (say so to the user)"
                 % (register, alt))


def _prune(base):
    try:
        dirs = [os.path.join(base, d) for d in os.listdir(base) if d.startswith("ctx-")]
    except OSError:
        return
    dirs.sort(key=lambda d: os.path.getmtime(d))
    for d in dirs[:-KEEP_DIRS]:
        shutil.rmtree(d, ignore_errors=True)


def build_context(pack, register, notes, context="", recipient="", subject="", budget=None,
                  strategy=None, person=None, seed=0, today=None, habits=None, restyle=None):
    """Select, assemble and write. Returns a dict of paths and counts (no message text)."""
    stats = read_json(os.path.join(pack, "stats.json"), {}) or {}
    d_budget, d_strategy = defaults(stats)
    budget = int(budget if budget is not None else d_budget)
    strategy = (strategy or d_strategy).upper()
    person = person or stats.get("person_default") or PERSON_DEFAULT
    habits = habits_default(stats, habits)
    if strategy not in S.STRATEGIES:
        raise ValueError("unknown strategy %r" % strategy)
    if not has_archive(pack):
        raise ValueError("this pack has no archive/ (a v1 pack); run `wlm build` to upgrade it")
    # like the old `kit`, a register with no archive borrows another one whole (framing and task too)
    reg, note = archive_register(pack, register)
    if not reg:
        raise ValueError(note)
    notes_out = [note] if note else []
    entries = read_jsonl(archive_path(pack, reg))
    person_text, person_used = load_person(pack, person, stats)
    learned_text = load_learned(pack, register, recipient)
    habits_text = H.habits_text(stats, reg, load_rules(pack)) if habits == "on" else ""
    habits_text, h_note = fit_habits(budget, learned_text, habits_text)
    if h_note:
        notes_out.append(h_note)
    h_tok = S.habits_tokens(habits_text)
    person_text, fit_note = fit_budget(budget - h_tok, person_text, learned_text)
    if fit_note:
        notes_out.append(fit_note)
        person_used = []
    p_tok = S.person_tokens(person_text)
    l_tok = S.learned_tokens(learned_text)
    request = {"context": context, "notes": notes, "subject": subject, "recipient": recipient}
    if strategy == "P":
        if not person_text:
            notes_out.append("note: strategy P needs a person file; none is included, so this is D")
        picked = S.select(entries, budget, dict(request, person_text=person_text, learned_text=learned_text,
                                                habits_text=habits_text), seed, "P")
    else:
        picked = S.select(entries, budget - p_tok - l_tok - h_tok, request, seed, strategy)
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    task = {"notes": notes, "context": context, "recipient": recipient, "subject": subject,
            "timestamp": today}
    system, task_text = S.assemble(person_text, picked, reg, task,
                                   complete=bool(picked) and len(picked) == len(entries),
                                   learned_text=learned_text, habits_text=habits_text)
    base = os.path.join(pack, "work")
    os.makedirs(base, mode=0o700, exist_ok=True)
    os.chmod(base, 0o700)   # also when the Write tool made it first (temp notes), with default modes
    d = os.path.join(base, "ctx-" + short_hash(system, task_text, n=12))
    os.makedirs(d, mode=0o700, exist_ok=True)
    sp, tp = os.path.join(d, "system.txt"), os.path.join(d, "task.txt")
    with private_open(sp) as f:
        f.write(system)
    with private_open(tp) as f:
        f.write(task_text + "\n")
    a_tok = S.total_tokens(picked)
    info = {"dir": d, "system": sp, "task": tp, "register": register, "archive_register": reg,
            "recipient": recipient, "strategy": strategy, "budget": budget, "seed": seed,
            "entries": len(picked), "archive_entries": len(entries), "archive_tokens": a_tok,
            "person": person if person_text else "none", "person_files": person_used,
            "person_tokens": p_tok, "learned_tokens": l_tok,
            "habits": "on" if habits_text else "off", "habits_tokens": h_tok, "tokens": a_tok + p_tok + l_tok + h_tok,
            "system_tokens": S.est_tokens(len(system)), "ids": [e.get("id") for e in picked],
            "restyle": restyle, "notes": notes_out}
    write_json(os.path.join(d, "context.json"), info)
    _prune(base)
    return info


def cmd_context(a):
    pack = pack_dir()
    if not os.path.exists(os.path.join(pack, "stats.json")):
        sys.exit("No voice pack at %s. Run /write-like-me:voice-setup first." % pack)
    restyle = None
    if a.restyle_file:
        text = _read(a.restyle_file, None).strip()
        if not text:
            sys.exit("the --restyle-file is empty")
        restyle = a.keep or "mine"
        platform = _platform(pack, a.register) if has_archive(pack) else ""
        notes = restyle_notes(text, a.register, restyle, recipient=a.recipient or "", subject=a.subject or "",
                              point=a.point or "", platform=platform)
    else:
        if a.keep or a.point:
            sys.exit("--keep and --point go with --restyle-file")
        notes = _read(a.notes_file, a.notes).strip()
        if not notes:
            sys.exit("give the notes for the message (--notes, --notes-file or --restyle-file)")
    try:
        info = build_context(pack, a.register, notes, context=_read(a.context_file, a.context).strip(),
                             recipient=a.recipient or "", subject=a.subject or "", budget=a.budget,
                             strategy=a.strategy, person=a.person, seed=a.seed, habits=a.habits,
                             restyle=restyle)
    except ValueError as e:
        sys.exit(str(e))
    for n in info["notes"]:
        print(n)
    print("system: %s" % info["system"])
    print("task: %s" % info["task"])
    print("entries: %d of %d (%s archive); tokens: %d of budget %d (archive %d, person %d, learned notes %d, "
          "habits %d); strategy: %s"
          % (info["entries"], info["archive_entries"], info["archive_register"], info["tokens"], info["budget"],
             info["archive_tokens"], info["person_tokens"], info["learned_tokens"], info["habits_tokens"],
             info["strategy"]))
    print("habits block: %s" % info["habits"])
    if info["person"] == "none":
        print("person file: none")
    else:
        print("person file: %s (%s)" % (info["person"], ", ".join(info["person_files"])))
    if restyle:
        print("restyle: keep %s%s" % (restyle, ", with your point" if (a.point or "").strip() else ""))
        print(RESTYLE_TIP)


def add_parser(sp):
    p = sp.add_parser("context", help="select archive entries within the budget and write the draft context")
    p.add_argument("--register", required=True, choices=REGISTERS)
    p.add_argument("--budget", type=int,
                   help="tokens of archive + person file + learned notes + habits block (default: stats.json)")
    p.add_argument("--strategy", type=str.upper, choices=sorted(S.STRATEGIES),
                   help="selection strategy (default: stats.json)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--notes")
    g.add_argument("--notes-file")
    g.add_argument("--restyle-file", help="text to rewrite in your voice (often an answer Claude wrote); "
                                          "builds the notes from it")
    p.add_argument("--keep", choices=KEEP_CHOICES,
                   help="with --restyle-file: mine (default) keeps only what you'd bother to mention, "
                        "at your length; all keeps the useful content")
    p.add_argument("--point", help="with --restyle-file: the point you want to make, put before the pasted text")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--context")
    g.add_argument("--context-file")
    p.add_argument("--recipient")
    p.add_argument("--subject")
    p.add_argument("--person", choices=["core", "full", "none"], help="person file tier (default: stats.json, else none)")
    p.add_argument("--habits", choices=HABITS_CHOICES,
                   help="the 'How my messages vary' block after the archive (default: stats.json habits_default, "
                        "else %s)" % build.HABITS_DEFAULT)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(fn=cmd_context)
    return p
