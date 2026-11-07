"""Prompt rendering from templates.

`context_template()` / `render_context()`: the archive context (`templates/context.md`, the pilot's
preface and "write the next entry" framing) that `select.assemble()` fills and `draft.py` sends.
`learned_for()`: the learned.md notes that apply to one draft (rendered into the same file).
`habits_block()`: the "How my messages vary" note after them (text from habits.py).
`render_task()`: the task for voice-check's plain baseline (templates/baseline.md).
Shared by the write-as-me skill and voice-check's scripted generation, so what gets evaluated is
what people use.
"""
import os
import re

from .llm import split_template
from .common import REGISTERS
from .retrieve import recipient_match

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(HERE)), "templates")
KIND = {
    "chat": ("chat message", "chat messages", "a chat app (Teams, Slack, iMessage)"),
    "email": ("email", "emails", "email"),
    "doc": ("passage", "passages of documents", "documents"),
    "prompt": ("prompt", "prompts to an AI assistant", "prompts to an AI assistant"),
    "post": ("comment", "posts and comments", "a public forum (Reddit)"),
}


def template(name):
    return split_template(os.path.join(TEMPLATES, name))


# Per-register wording for templates/context.md. For `post` it reproduces the pilot's preface.
CONTEXT_WORDS = {
    "chat": {"kinds": "chat messages", "kind": "message", "wrote": "sent",
             "entry_shows": "who it was to, the date, a shortened version of the message I was replying to "
                            "(when there was one)",
             "new_one": "a new message I'm replying to"},
    "email": {"kinds": "emails", "kind": "email", "wrote": "sent",
              "entry_shows": "who it was to, the date, a shortened version of the email I was replying to "
                             "(when there was one)",
              "new_one": "a new email I'm replying to"},
    "post": {"kinds": "posts and comments", "kind": "comment", "wrote": "posted",
             "entry_shows": "where I posted it, the date, a shortened version of the post or comment I was "
                            "replying to",
             "new_one": "a new post I'm replying to"},
    "doc": {"kinds": "passages from my documents", "kind": "passage", "wrote": "wrote",
            "entry_shows": "the date, a shortened version of what the passage followed (when there was "
                           "something)",
            "new_one": "the context for a new passage"},
    "prompt": {"kinds": "prompts to an AI assistant", "kind": "prompt", "wrote": "wrote",
               "entry_shows": "the date, a shortened version of the assistant's reply I was answering (when "
                              "there was one)",
               "new_one": "the assistant's latest reply"},
}
CONTEXT_KEYS = ("archive", "wrote", "entry_shows", "kind", "new_one", "person", "person_text", "entries", "task",
                "learned", "learned_text", "habits", "habits_text")


def _fill_once(tmpl, values):
    """Replace {key} placeholders in one pass, so text substituted in (real messages, which may
    contain braces) is never itself searched for placeholders."""
    return re.sub(r"\{(%s)\}" % "|".join(CONTEXT_KEYS), lambda m: str(values.get(m.group(1), m.group(0))), tmpl)


def context_sections():
    """{system, person, learned, habits, user} template text from templates/context.md."""
    system, user = template("context.md")
    system, rest = system.split("=== PERSON ===", 1)
    person, rest = rest.split("=== LEARNED ===", 1)
    learned, habits = rest.split("=== HABITS ===", 1)
    return {"system": system.strip(), "person": person.strip(), "learned": learned.strip(),
            "habits": habits.strip(), "user": user}


def context_template():
    """(system, person_block, user) template text from templates/context.md."""
    t = context_sections()
    return t["system"], t["person"], t["user"]


def learned_block(learned_text):
    """The learned-notes block as render_context inserts it ('' for no notes)."""
    if not (learned_text or "").strip():
        return ""
    return _fill_once(context_sections()["learned"], {"learned_text": learned_text.strip()})


def habits_block(habits_text):
    """The "How my messages vary" block as render_context inserts it ('' for no habits text):
    `habits_text` is habits.habits_text() for this draft's register."""
    if not (habits_text or "").strip():
        return ""
    return _fill_once(context_sections()["habits"], {"habits_text": habits_text.strip()})


def archive_phrase(register, first="", last="", complete=False):
    """'a selection of my chat messages from 2024 to 2026' (the pilot: 'the complete archive of ...')."""
    kinds = CONTEXT_WORDS.get(register, CONTEXT_WORDS["chat"])["kinds"]
    head = ("the complete archive of my %s" if complete else "a selection of my %s") % kinds
    if first and last and first != last:
        return "%s from %s to %s" % (head, first, last)
    return "%s from %s" % (head, first or last) if (first or last) else head


def render_context(register, entries_text, task, person_text="", first="", last="", complete=False,
                   learned_text="", habits_text=""):
    """Fill templates/context.md. Returns (system_file_text, task_text). `entries_text` is the
    rendered entries (pilot format, oldest first); `task` the new entry with an empty body;
    `person_text` the person file tier, or "" for none (the person block is then left out);
    `learned_text` the learned.md notes for this draft (`learned_for`), or "" for none (the notes
    block, after the archive, is then left out); `habits_text` the measured habits of the register
    (habits.habits_text), or "" for none: the "How my messages vary" block after the notes."""
    system, person_block, user = context_template()
    w = dict(CONTEXT_WORDS.get(register, CONTEXT_WORDS["chat"]))
    w["archive"] = archive_phrase(register, first, last, complete)
    person = ""
    if (person_text or "").strip():
        person = _fill_once(person_block, {"person_text": person_text.strip()}) + "\n\n"
    learned = learned_block(learned_text)
    learned = "\n\n" + learned if learned else ""
    habits = habits_block(habits_text)
    habits = "\n\n" + habits if habits else ""
    sys_text = _fill_once(system, dict(w, person=person, entries=(entries_text or "").strip(), learned=learned,
                                       habits=habits))
    return sys_text + "\n", _fill_once(user, dict(w, task=(task or "").rstrip())).strip()


def render_task(context, notes, recipient="", register="chat", subject=""):
    parts = []
    if recipient:
        parts.append("Recipient: %s" % recipient)
    if subject and register == "email":
        parts.append("Subject: %s" % subject)
    ctx = (context or "").strip()
    if register in ("chat", "email"):
        parts.append("Message I'm replying to (may be empty if I'm starting the thread):\n<<<\n%s\n>>>"
                     % (ctx or "(none: I'm starting this one)"))
    elif register == "post":
        parts.append("Post or comment I'm replying to (may be empty if I'm starting a new post):\n<<<\n%s\n>>>"
                     % (ctx or "(none: this is a new post)"))
    elif ctx:
        parts.append("Context:\n<<<\n%s\n>>>" % ctx)
    parts.append("What I want to say:\n%s" % (notes or "").strip())
    return "\n\n".join(parts)


NOTE_HEAD = re.compile(r"^##\s+(?:\d{4}-\d{2}-\d{2}\s+)?([\w-]+)(?:\s+\(to (.*)\))?\s*$")


def learned_for(learned_text, register, recipient=""):
    """The learned.md notes that apply to this draft. Notes are blocks headed
    `## <date> <register> [(to <name>)]` (what `wlm learn note` writes). A block applies when its
    register is this one or `all`, and it names no recipient or this recipient. Text before the
    first `## ` heading (the file's own explanation) is never sent. A `## ` heading in another
    shape (hand-written) is kept for every draft."""
    out, keep, block = [], False, []
    for line in (learned_text or "").splitlines() + ["## end-of-file"]:
        if line.startswith("## "):
            if keep and block:
                out.append("\n".join(block).strip())
            m = NOTE_HEAD.match(line)
            if line == "## end-of-file":
                break
            if m and m.group(1).lower() in REGISTERS + ("all",):
                to = (m.group(2) or "").strip()
                keep = m.group(1).lower() in (register, "all") and (
                    not to or bool(recipient) and recipient_match({"recipient": to}, recipient))
            else:
                keep = True
            block = [line]
        elif block:
            block.append(line)
    return "\n\n".join(b for b in out if "\n" in b)  # skip headings with no note lines


def learned_notes(learned_text, register, recipient=""):
    """learned_for() as the draft's context shows it: the note lines, without the `## <date> <register>`
    headings `wlm learn note` writes (a hand-written heading keeps its text)."""
    out = []
    for line in learned_for(learned_text, register, recipient).splitlines():
        if line.startswith("## "):
            m = NOTE_HEAD.match(line)
            if m and m.group(1).lower() in REGISTERS + ("all",):
                continue
            line = line[3:].strip()
        if line.strip():
            out.append(line)
    return "\n".join(out).strip()
