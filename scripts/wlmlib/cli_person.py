"""`wlm person`: build, show and edit the person file (see person.py for the format).

    wlm person build [--which personal|work|both] [--include-unsorted] [--yes] [--model M]
        Prints the call estimate first. Without --yes it is a dry run: no model call, nothing written.
        --which defaults to personal; person.work.md is built only when asked for (work or both).
        Docs and chat from `extract files`, pasted samples and `learn add` may be work material, so
        person.md leaves them out unless --include-unsorted says they are personal.
    wlm person show [--tier core|full] [--which personal|work|both] [--ids]
        The tier exactly as `wlm context` sends it (default core, both files). --ids lists each
        cited sentence with its claim ids, and the user-stated claims, for `forget`.
    wlm person add --text "..." [--work]
        A claim the user states, cited as user-stated, added to the core tier of person.md
        (person.work.md with --work). It survives rebuilds.
    wlm person forget <claim id or text> [--work]
        Deletes the claim (cN, uN, or its claim or sentence text) and every sentence resting on it,
        and blacklists it in person.blacklist.json so a rebuild never brings it back.
"""
import os
import sys

from . import person as P
from .archive import has_archive
from .common import pack_dir

WHICH = {"personal": ["personal"], "work": ["work"], "both": ["personal", "work"]}
LABELS = {"post": "posts", "chat": "personal chat", "doc": "docs", "teams": "Teams",
          "outlook": "Outlook", "claude_code": "Claude Code prompts", "proofhq": "ProofHQ comments"}
UNSORTED_LABELS = {"file": "docs and chat from `extract files`", "pasted": "pasted chat and doc samples",
                   "learned": "docs added by `learn add`"}


def _pack():
    pack = pack_dir()
    if not os.path.exists(os.path.join(pack, "stats.json")):
        sys.exit("No voice pack at %s. Run /write-like-me:voice-setup first." % pack)
    return pack


def print_estimate(est, kinds, unsorted=None, include_unsorted=False):
    lo = hi = 0
    for k in ("personal", "work"):
        if k not in kinds:
            print("%s -> %s: not requested (add --which %s or both)" % (k, P.FILES[k], k))
            continue
        e = est[k]
        if not e["entries_total"]:
            print("%s -> %s: no entries in the pack; it will not be built" % (k, e["file"]))
            continue
        parts = ", ".join("%s %d" % (LABELS.get(lab, lab), n) for lab, n in e["entries"].items())
        print("%s -> %s: %d entries (%s) in %d chunks" % (k, e["file"], e["entries_total"], parts, e["chunks"]))
        print("  model calls: about %d to %d (map %d, reduce up to %d, write %d to %d)"
              % (e["calls_min"], e["calls_max"], e["chunks"], e["reduce_max"], e["write_min"], e["write_max"]))
        lo += e["calls_min"]
        hi += e["calls_max"]
    if "personal" in kinds and unsorted:
        parts = ", ".join("%s %d" % (UNSORTED_LABELS.get(src, src), n) for src, n in sorted(unsorted.items()))
        if include_unsorted:
            print("included as personal (--include-unsorted): %s" % parts)
        else:
            print("left out of %s because they may be work material: %s; add --include-unsorted only if all "
                  "of them are personal" % (P.FILES["personal"], parts))
    print("total: about %d to %d model calls through your Claude Code login (claude -p); the text sent is "
          "your own archive entries for the files above" % (lo, hi))
    return hi


def cmd_build(a):
    pack = _pack()
    if not has_archive(pack):
        sys.exit("this pack has no archive/ (a v1 pack); run `wlm build` to upgrade it")
    kinds = WHICH[a.which]
    est = P.estimate(pack, kinds, include_unsorted=a.include_unsorted)
    hi = print_estimate(est, kinds, P.count_unsorted(pack), a.include_unsorted)
    if not a.yes:
        print("dry run: nothing was called or written. Re-run with --yes to build.")
        return
    if not hi:
        sys.exit("nothing to build")
    log = lambda s: print(s, file=sys.stderr, flush=True)  # noqa: E731
    for k in kinds:
        if not est[k]["entries_total"]:
            continue
        try:
            st = P.build(pack, k, model=a.model, log=log, include_unsorted=a.include_unsorted)
        except RuntimeError as e:
            sys.exit("%s: build failed (%s); finished calls are cached, so re-running costs only the rest"
                     % (P.FILES[k], e))
        if st is None:
            continue
        chk = st.get("check", {})
        kept = sum(v.get("kept", 0) for v in chk.values())
        dropped = sum(v.get("dropped_unmarked", 0) + v.get("dropped_unknown_marker", 0) for v in chk.values())
        calls = st.get("calls", {})
        made = sum(v for c, v in calls.items() if c.endswith("_made"))
        cached = sum(v for c, v in calls.items() if c.endswith("_cached"))
        print("%s: %d claims (%d single-source facts, %d stated by you); dropped %d for fewer than two "
              "citations, %d blacklisted; %d sentences kept, %d uncited dropped; core ~%d tokens, full ~%d "
              "tokens; %d calls made, %d from cache"
              % (st["file"], st["claims"], st["single_source_facts"], st["user_claims"],
                 st["dropped_for_citations"], st["dropped_by_blacklist"], kept, dropped,
                 st["tokens"]["core"], st["tokens"]["full"], made, cached))
        print("  wrote %s and %s" % (os.path.join(pack, st["file"]), os.path.join(pack, P.SIDECAR)))


def cmd_show(a):
    pack = _pack()
    shown = 0
    for k in WHICH[a.which]:
        name = P.FILES[k]
        if not os.path.isfile(P.person_path(pack, k)):
            print("== %s: none ==" % name)
            continue
        if a.ids:
            lines = P.show_ids(pack, k, a.tier)
            print("== %s (%s, claim ids) ==" % (name, a.tier))
            print("\n".join(lines) if lines else "(no citations: this file was not built by `wlm person build`)")
        else:
            text = P.read_tier(pack, k, a.tier)
            print("== %s (%s, ~%d tokens) ==" % (name, a.tier, P.tokens(text)))
            print(text or "(empty)")
        print()
        shown += 1
    n = len(P.load_blacklist(pack)["claims"])
    if n:
        print("blacklist: %d forgotten claims a rebuild will not bring back (%s)" % (n, P.BLACKLIST))
    if not shown:
        print("no person file yet: `wlm person build` (dry run first) or `wlm person add --text ...`")


def cmd_add(a):
    pack = _pack()
    kind = "work" if a.work else "personal"
    try:
        uid, new = P.add(pack, a.text, kind)
    except ValueError as e:
        sys.exit(str(e))
    if new:
        print("added %s to %s (core tier, cited as user-stated)" % (uid, P.FILES[kind]))
    else:
        print("%s already has that claim as %s" % (P.FILES[kind], uid))


def cmd_forget(a):
    pack = _pack()
    kind = "work" if a.work else "personal"
    try:
        r = P.forget(pack, a.target, kind)
    except ValueError as e:
        sys.exit(str(e))
    if r["matched_by"] == "text only":
        print("no current claim in %s matches; blacklisted the text so a rebuild will not add it" % r["file"])
    else:
        print("forgot %s in %s (matched by %s): %d sentences and %d stated claims removed; blacklisted"
              % (", ".join(r["claims"]), r["file"], r["matched_by"], r["sentences_removed"], r["user_claims_removed"]))


def cmd_person(a):
    return {"build": cmd_build, "show": cmd_show, "add": cmd_add, "forget": cmd_forget}[a.person_cmd](a)


def add_parser(sp):
    p = sp.add_parser("person", help="the person file: a cited description of what you know and think")
    psp = p.add_subparsers(dest="person_cmd", required=True)

    b = psp.add_parser("build", help="distil person.md / person.work.md from the archive (dry run without --yes)")
    b.add_argument("--which", choices=sorted(WHICH), default="personal",
                   help="personal (person.md, the default), work (person.work.md) or both")
    b.add_argument("--include-unsorted", action="store_true",
                   help="person.md: also use docs and chat from `extract files`, pasted samples and `learn add` "
                        "(only if they are all personal, not work)")
    b.add_argument("--yes", action="store_true", help="make the model calls (default: dry run with the estimate)")
    b.add_argument("--model", help="model for the calls (default: $WRITE_LIKE_ME_MODEL, else your default)")

    s = psp.add_parser("show", help="print a tier as the drafter sees it")
    s.add_argument("--tier", choices=["core", "full"], default="core")
    s.add_argument("--which", choices=sorted(WHICH), default="both")
    s.add_argument("--ids", action="store_true", help="list cited sentences with their claim ids")

    d = psp.add_parser("add", help="add a claim you state yourself (cited as user-stated)")
    d.add_argument("--text", required=True)
    d.add_argument("--work", action="store_true", help="add it to person.work.md")

    f = psp.add_parser("forget", help="delete a claim and blacklist it so rebuilds never bring it back")
    f.add_argument("target", help="claim id (c12, u2) or the claim's or sentence's text")
    f.add_argument("--work", action="store_true", help="in person.work.md")

    p.set_defaults(fn=cmd_person)
    return p
