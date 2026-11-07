"""Render the draft / critique / revise prompts (the run-1 "F" pipeline) from templates.

Shared by the interactive write-as-me skill (via `wlm.py kit`) and by voice-check's scripted
generation, so what gets evaluated is what people use.
"""
import os
import re

from .llm import fill, split_template
from .common import REGISTERS
from .retrieve import format_examples, recipient_match, retrieve
from .rules import rules_section

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(HERE)), "templates")
KIND = {
    "chat": ("chat message", "chat messages", "a chat app (Teams, Slack, iMessage)"),
    "email": ("email", "emails", "email"),
    "doc": ("passage", "passages of documents", "documents"),
    "prompt": ("prompt", "prompts to an AI assistant", "prompts to an AI assistant"),
}
SUPPLEMENT_MIN = 15   # below this many samples, borrow mechanics from another register


def template(name):
    return split_template(os.path.join(TEMPLATES, name))


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
    elif ctx:
        parts.append("Context:\n<<<\n%s\n>>>" % ctx)
    parts.append("What I want to say:\n%s" % (notes or "").strip())
    return "\n\n".join(parts)


def length_target(stats, register):
    F = ((stats.get("registers") or {}).get(register) or {}).get("features") or {}
    if not F.get("len_median"):
        return "short"
    return "about %d words; usually %d-%d, rarely over %d" % (
        round(F["len_median"]), round(F.get("len_p25", 0)), round(F.get("len_p75", 0)), round(F.get("len_p90", 0)))


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


def build_kit(bank, stats, rules_text, learned_text, register, notes, context="", recipient="",
              subject="", k=3, n_critic=5, exclude_ids=(), heldout=None):
    """Assemble everything for one draft. Returns a dict with the rendered draft prompt
    (system + user), the critic reference messages and metadata."""
    reg_info = (stats.get("registers") or {}).get(register) or {}
    fixed_ids = reg_info.get("fixed_ids") or []
    query = "%s %s %s" % (context or "", notes or "", subject or "")
    R = retrieve(bank, register, query, recipient=recipient, k=k, n_critic=n_critic,
                 fixed_ids=fixed_ids, exclude_ids=exclude_ids, heldout=heldout)
    examples = R["fixed"] + R["retrieved"]
    supplement = []
    if reg_info.get("n", 0) < SUPPLEMENT_MIN:
        # thin register: borrow mechanics from the richest other register (prompt history last)
        others = sorted(((r, d) for r, d in (stats.get("registers") or {}).items() if r != register),
                        key=lambda rd: (rd[0] == "prompt", -rd[1].get("n", 0)))
        if others:
            o = others[0][0]
            S = retrieve(bank, o, query, k=3, n_critic=0, fixed_ids=(stats["registers"][o].get("fixed_ids") or [])[:3],
                         exclude_ids=exclude_ids, heldout=heldout)
            supplement = (S["fixed"] + S["retrieved"])[:4]
    kind, kinds, medium = KIND.get(register, KIND["chat"])
    ex_text = format_examples(examples, register)
    if supplement:
        ex_text += "\n\n" + format_examples(supplement, register, start=len(examples) + 1)
    n_ret = len(R["retrieved"])
    same = ("from my earlier messages to this same person" if R["same_recipient"] else
            "closest in situation; none to this person")
    rules = rules_section(rules_text, register)
    learned = learned_for(learned_text, register, recipient)
    if learned:
        rules += "\n\nLearned from my edits:\n" + learned
    system, user = template("draft.md")
    user = fill(user, kind=kind, kinds=kinds, medium=medium, examples=ex_text, n_retrieved=n_ret,
                same_note=same, rules=rules or "(none yet)",
                task=render_task(context, notes, recipient, register, subject),
                length_target=length_target(stats, register))
    if not n_ret:
        user = re.sub(r"\nThe last 0 examples.*\n", "\n", user)
    return {"register": register, "recipient": recipient, "system": system, "user": user,
            "task": render_task(context, notes, recipient, register, subject),
            "examples": [r["id"] for r in examples + supplement],
            "critic": [{"id": r["id"], "text": r["text"], "context": r.get("context", "")} for r in R["critic"]],
            "same_recipient": R["same_recipient"], "length_target": length_target(stats, register)}


def critique_prompt(kit, draft, stats):
    kind, kinds, medium = KIND.get(kit["register"], KIND["chat"])
    refs = "\n\n".join("[%d] %s" % (i + 1, r["text"]) for i, r in enumerate(kit["critic"]))
    F = ((stats.get("registers") or {}).get(kit["register"]) or {}).get("features") or {}
    system, user = template("critique.md")
    return system, fill(user, n=len(kit["critic"]), kind=kind, kinds=kinds, medium=medium,
                        refs=refs or "(no reference messages available)", task=kit["task"], draft=draft.strip(),
                        median=int(round(F.get("len_median", 0))) or "?",
                        imperfection=F.get("imperfection_level", "unknown"))


def revise_prompt(kit, draft, critique, lint_findings=()):
    system, user = template("revise.md")
    lint_txt = "\n".join("- %s" % f["message"] for f in lint_findings if f["level"] in ("fix", "consider"))
    return system, fill(user, original_prompt=kit["user"], draft=draft.strip(), critique=critique.strip(),
                        lint=lint_txt or "- nothing")
