#!/usr/bin/env python3
"""write-like-me command line. Python 3.9+ standard library only. Run as:

    python3 -I "$PLUGIN/scripts/wlm.py" <command> [options]

Commands (all data stays in the voice pack: $WRITE_LIKE_ME_PACK or ~/.claude/voice-pack):
  discover                         which sources exist on this machine (metadata only)
  extract <source> [...]           claude_code | teams | outlook | files PATH | imessage
  vendor-install teams [--yes]     install the optional Teams parser at pinned commits (dry run first)
  add-samples --register R --file F   pasted samples, separated by lines of ---
  build                            clean + measure + write profile.md, rules.md, stats.json, bank
  status                           what's in the pack (counts only)
  kit --register R --notes ...     assemble the draft prompt (exemplars + rules) for one message
  critique-prompt --kit D --draft-file F   render the critic's prompt for a subagent
  critique --kit D --draft-file F  run the critic via `claude -p` (fallback when no subagent)
  lint --register R --file F       deterministic checks against rules and measured habits
  log-draft --kit D --file F       remember a final draft (caricature tracking, voice-learn)
  learn diff|add|note|rule         learn from what you actually sent
  check prepare|generate|page|score   blind test + scoring
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir

from wlmlib import build as B  # noqa: E402
from wlmlib import check as CK  # noqa: E402
from wlmlib import compose, learn, lint as LN, llm  # noqa: E402
from wlmlib.common import (REGISTERS, append_jsonl, ensure_pack, now_iso, pack_dir, private_open, read_json, read_jsonl,  # noqa: E402
                           short_hash, write_json, write_jsonl)
from wlmlib.discover import discover  # noqa: E402


def _read(path_or_none, text_or_none):
    if path_or_none:
        with open(os.path.expanduser(path_or_none), encoding="utf-8") as f:
            return f.read()
    return text_or_none or ""


def _counts(recs):
    out = {}
    for r in recs:
        out[r["register"]] = out.get(r["register"], 0) + 1
    return out


def _need_pack():
    p = pack_dir()
    if not os.path.exists(os.path.join(p, "stats.json")):
        sys.exit("No voice pack at %s. Run /write-like-me:voice-setup first." % p)
    return p


def cmd_discover(a):
    print(json.dumps(discover(), indent=1))


def cmd_extract(a):
    pack = ensure_pack()
    stats = {}
    src = a.source
    try:
        if src == "claude_code":
            from wlmlib.sources import claude_code
            import collections
            st = collections.defaultdict(int)
            recs = claude_code.extract(a.claude_dir, ai_overlap_check=not a.no_ai_check, stats=st)
            stats = {k: (dict(v) if isinstance(v, dict) else v) for k, v in st.items()}
            name = "claude_code"
        elif src == "teams":
            from wlmlib.sources import teams
            recs = teams.extract(stats=stats)
            name = "teams"
        elif src == "outlook":
            from wlmlib.sources import outlook_mac
            recs = outlook_mac.extract(addresses=a.address or (), names=a.name or (), stats=stats)
            name = "outlook"
        elif src == "files":
            from wlmlib.sources import files
            if not a.path:
                sys.exit("extract files needs a PATH")
            recs = files.extract(a.path, addresses=a.address or (), all_mine=a.all_mine, names=a.name or (),
                                 doc_register=a.register or "doc", stats=stats)
            name = "files-" + short_hash(os.path.abspath(os.path.expanduser(a.path)), n=8)
        elif src == "imessage":
            from wlmlib.sources import imessage
            recs = imessage.extract(export_dir=a.export_dir, start_date=a.since, stats=stats)
            name = "imessage"
        else:
            sys.exit("unknown source %s" % src)
    except RuntimeError as e:
        print(json.dumps({"source": src, "ok": False, "error": str(e)}))
        sys.exit(2)
    out = os.path.join(pack, "sources", name + ".jsonl")
    if not recs and os.path.exists(out) and read_jsonl(out) and not a.replace_empty:
        # a mistyped --address or an unreadable folder must not wipe samples extracted earlier
        print(json.dumps({"source": src, "ok": False, "file": out,
                          "error": "found 0 messages this time; kept the %d already extracted from this source "
                                   "(re-run with --replace-empty to clear it)" % len(read_jsonl(out)),
                          "stats": {k: v for k, v in stats.items() if not isinstance(v, (list,)) or len(v) < 20}},
                         default=str))
        sys.exit(2)
    write_jsonl(out, recs)
    os.chmod(out, 0o600)
    print(json.dumps({"source": src, "ok": True, "file": out, "records": len(recs), "by_register": _counts(recs),
                      "stats": {k: v for k, v in stats.items() if not isinstance(v, (list,)) or len(v) < 20}},
                     default=str))


def cmd_vendor_install(a):
    from wlmlib.sources import teams
    vd = teams.vendor_dir()
    cmds = teams.vendor_commands()
    print("This downloads third-party code (MIT, CCL Solutions Group) from GitHub at pinned commits into %s:" % vd)
    for name, url, sha in teams.VENDOR:
        print("  %s  %s @ %s" % (name, url, sha[:12]))
    print("commands:")
    for _, c in cmds:
        print("  " + " ".join(c))
    if not a.yes:
        print("dry run: nothing was run. Re-run with --yes to install.")
        return
    os.makedirs(vd, exist_ok=True)
    for name, url, sha in teams.VENDOR:
        if os.path.isdir(os.path.join(vd, name)):
            sys.exit("%s already exists; delete it first to reinstall" % os.path.join(vd, name))
    for _, c in cmds:
        r = subprocess.run(c)
        if r.returncode != 0:
            sys.exit("failed: %s" % " ".join(c))
    ok, why = teams.available()
    print("installed. teams: %s" % why)


def cmd_add_samples(a):
    from wlmlib.sources import paste
    pack = ensure_pack()
    recs = paste.parse(_read(a.file, a.text), a.register, a.recipient or "")
    p = os.path.join(pack, "sources", "pasted.jsonl")
    for r in recs:
        append_jsonl(p, r)
    os.chmod(p, 0o600)
    print(json.dumps({"added": len(recs), "register": a.register, "file": p}))


def cmd_build(a):
    pack = ensure_pack()
    stats = B.build(pack, mask=not a.no_mask)
    print(B.summary(stats))
    print("pack: %s" % pack)


def cmd_status(a):
    pack = pack_dir()
    stats = read_json(os.path.join(pack, "stats.json"))
    if not stats:
        print(json.dumps({"pack": pack, "built": False}))
        return
    srcs = sorted(f[:-6] for f in os.listdir(os.path.join(pack, "sources")) if f.endswith(".jsonl"))
    drafts = len(read_jsonl(os.path.join(pack, "drafts.jsonl")))
    print(B.summary(stats))
    print("sources: %s; drafts logged: %d; pack: %s" % (", ".join(srcs), drafts, pack))


def _new_work_dir(pack):
    base = os.path.join(pack, "work")
    os.makedirs(base, mode=0o700, exist_ok=True)
    old = sorted(os.listdir(base))
    for d in old[:-30]:
        shutil.rmtree(os.path.join(base, d), ignore_errors=True)
    d = os.path.join(base, now_iso().replace(":", "") + "-" + short_hash(os.getpid(), now_iso(), n=4))
    os.makedirs(d, mode=0o700)
    return d


def cmd_kit(a):
    pack = _need_pack()
    stats, bank, rules = B.load_pack(pack)
    learned = _read(os.path.join(pack, "learned.md"), None) if os.path.exists(os.path.join(pack, "learned.md")) else ""
    reg = a.register
    have = list((stats.get("registers") or {}).keys())
    if reg not in have:
        order = {"chat": ["email", "doc", "prompt"], "email": ["chat", "doc", "prompt"],
                 "doc": ["email", "chat", "prompt"], "prompt": ["chat", "email", "doc"]}[reg]
        alt = next((r for r in order if r in have), None)
        if not alt:
            sys.exit("the pack has no samples at all; run voice-setup")
        print("note: no %s samples in the pack; using %s samples instead (say so to the user)" % (reg, alt))
        reg = alt
    kit = compose.build_kit(bank, stats, rules, learned, reg, _read(a.notes_file, a.notes),
                            context=_read(a.context_file, a.context), recipient=a.recipient or "",
                            subject=a.subject or "", k=a.k)
    d = _new_work_dir(pack)
    write_json(os.path.join(d, "kit.json"), kit)
    with private_open(os.path.join(d, "draft_prompt.md")) as f:
        f.write("SYSTEM:\n%s\n\nUSER:\n%s\n" % (kit["system"], kit["user"]))
    print("kit: %s" % d)
    print("exemplars: %d (same recipient: %d); critic refs: %d; length target: %s"
          % (len(kit["examples"]), kit["same_recipient"], len(kit["critic"]), kit["length_target"]))
    print("----- draft prompt -----")
    print(kit["user"])


def _kit(d):
    k = read_json(os.path.join(d, "kit.json"))
    if not k:
        sys.exit("no kit.json in %s" % d)
    return k


def cmd_critique_prompt(a):
    pack = _need_pack()
    stats, _, _ = B.load_pack(pack)
    kit = _kit(a.kit)
    s, u = compose.critique_prompt(kit, _read(a.draft_file, a.draft), stats)
    p = os.path.join(a.kit, "critique_prompt.md")
    with private_open(p) as f:
        f.write("%s\n\n%s\n" % (s, u))
    print(p)


def cmd_critique(a):
    pack = _need_pack()
    stats, _, _ = B.load_pack(pack)
    kit = _kit(a.kit)
    s, u = compose.critique_prompt(kit, _read(a.draft_file, a.draft), stats)
    print(llm.call(s, u, model=a.model))


def cmd_lint(a):
    pack = _need_pack()
    stats, _, rules = B.load_pack(pack)
    text = _read(a.file, a.text)
    recent = read_jsonl(os.path.join(pack, "drafts.jsonl"))
    res = LN.lint(text, a.register, rules, stats, recent)
    print(json.dumps(res, indent=1) if a.json else LN.format_report(res))


def cmd_log_draft(a):
    pack = _need_pack()
    kit = _kit(a.kit) if a.kit else {"register": a.register, "recipient": a.recipient or ""}
    text = _read(a.file, a.text).strip()
    row = {"at": now_iso(), "register": kit["register"] if a.kit else a.register, "recipient": kit.get("recipient", ""),
           "text": text, "kit": a.kit or ""}
    append_jsonl(os.path.join(pack, "drafts.jsonl"), row)
    if a.kit:
        with private_open(os.path.join(a.kit, "final.txt")) as f:
            f.write(text + "\n")
    print("logged")


def cmd_learn(a):
    pack = _need_pack()
    stats, _, rules = B.load_pack(pack)
    if a.action == "diff":
        draft = _read(a.draft_file, a.draft)
        if not draft:
            drafts = [d for d in read_jsonl(os.path.join(pack, "drafts.jsonl")) if d.get("register") == a.register]
            if not drafts:
                sys.exit("no draft given and none logged for %s" % a.register)
            draft = drafts[-1]["text"]
        rep = learn.report(draft, _read(a.sent_file, a.sent), a.register, stats, rules)
        print(json.dumps(rep, indent=1) if a.json else learn.format_report(rep))
    elif a.action == "add":
        from wlmlib.common import make_record
        text = _read(a.sent_file, a.sent).strip()
        if not text:
            sys.exit("nothing to add")
        rec = make_record("learned", a.register, text, timestamp=now_iso(), recipient=a.recipient or "",
                          thread=a.recipient or "", context=_read(a.context_file, a.context))
        p = os.path.join(pack, "sources", "learned.jsonl")
        append_jsonl(p, rec)
        os.chmod(p, 0o600)
        print("added to bank source %s (run `build` to refresh stats)" % p)
    elif a.action == "note":
        lines = a.text or []
        with private_open(os.path.join(pack, "learned.md"), "a") as f:
            f.write(learn.note_block(lines, a.register, a.recipient or ""))
        print("noted %d line(s) in learned.md" % len(lines))
    elif a.action == "rule":
        from wlmlib.rules import KEEP_MARK
        rp = os.path.join(pack, "rules.md")
        with open(rp, encoding="utf-8") as f:
            txt = f.read()
        if KEEP_MARK not in txt:
            txt += "\n" + KEEP_MARK + "\n"
        add = "\n## %s\n\n%s\n" % (a.register, "\n".join(l if l.startswith("- ") else "- " + l for l in (a.text or [])))
        with private_open(rp) as f:
            f.write(txt.rstrip() + "\n" + add)
        print("added %d rule(s) to rules.md (kept across rebuilds)" % len(a.text or []))


def cmd_check(a):
    pack = _need_pack()
    stats, bank, rules = B.load_pack(pack)
    learned = _read(os.path.join(pack, "learned.md"), None) if os.path.exists(os.path.join(pack, "learned.md")) else ""
    if a.action == "prepare":
        items = CK.select_items(bank, stats, a.register, n=a.n)
        if len(items) < 4:
            sys.exit("only %d usable %s messages; need more samples for a meaningful test" % (len(items), a.register))
        run = os.path.join(pack, "check", now_iso()[:19].replace(":", "") + "-" + a.register)
        os.makedirs(run, mode=0o700, exist_ok=True)
        write_jsonl(os.path.join(run, "items.jsonl"), items)
        demo = a.demo or any(i.get("source") == "synthetic" for i in items)
        label = ("synthetic fixture data, not anyone's real messages" if demo
                 else "your real messages from the voice pack at %s" % pack)
        write_json(os.path.join(run, "meta.json"), {"register": a.register, "created": now_iso(), "n": len(items),
                                                    "demo": demo, "label": label})
        print(json.dumps({"run": run, "items": len(items), "data": ("DEMO: " if demo else "REAL: ") + label,
                          "model_calls_estimate": len(items) * 5 + (len(items) * 4 + 5) // 6}))
        return
    run = a.run or _latest_run(pack)
    if a.action == "generate":
        if not llm.available():
            sys.exit("the claude CLI is not on PATH")
        rows = CK.generate(run, bank, stats, rules, learned, model=a.model, workers=a.workers,
                           log=lambda s: print(s, file=sys.stderr))
        print(json.dumps({"run": run, "generations": len(rows)}))
    elif a.action == "page":
        out, n = CK.build_page(run, conditions=("voice", "plain") if a.include_plain else ("voice",))
        print(json.dumps({"page": out, "pairs": n}))
        if a.open:
            if sys.platform == "darwin":
                subprocess.run(["open", out])
            else:
                import webbrowser
                if not webbrowser.open("file://" + os.path.abspath(out)):
                    print("could not open a browser; open this file manually: %s" % out)
    elif a.action == "score":
        if a.picks:
            picks = read_json(os.path.expanduser(a.picks))
            try:
                CK.score_picks(picks, run)  # validate before copying and before any judge calls
            except CK.PicksMismatch as e:
                sys.exit("picks not used: %s" % e)
            shutil.copy(os.path.expanduser(a.picks), os.path.join(run, "picks.json"))
        res = CK.score(run, bank, stats, model=a.model, judge=not a.no_judge, log=lambda s: print(s, file=sys.stderr))
        print(CK.format_scores(res))


def _latest_run(pack):
    base = os.path.join(pack, "check")
    runs = sorted(os.listdir(base)) if os.path.isdir(base) else []
    if not runs:
        sys.exit("no check runs yet; run `check prepare` first")
    return os.path.join(base, runs[-1])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="wlm.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("discover").set_defaults(fn=cmd_discover)

    p = sp.add_parser("extract")
    p.add_argument("source", choices=["claude_code", "teams", "outlook", "files", "imessage"])
    p.add_argument("--replace-empty", action="store_true",
                   help="allow a run that finds 0 messages to clear this source's earlier extract")
    p.add_argument("path", nargs="?")
    p.add_argument("--address", action="append", help="your own email address (repeatable)")
    p.add_argument("--name", action="append", help="name/initials you sign emails with (repeatable)")
    p.add_argument("--all-mine", action="store_true", help="every email in PATH was sent by you")
    p.add_argument("--register", choices=REGISTERS, help="register for .txt/.md files (default doc)")
    p.add_argument("--claude-dir")
    p.add_argument("--no-ai-check", action="store_true", help="skip the slow pasted-Claude-text check")
    p.add_argument("--export-dir", help="existing imessage-exporter txt export")
    p.add_argument("--since", help="imessage start date YYYY-MM-DD")
    p.set_defaults(fn=cmd_extract)

    p = sp.add_parser("vendor-install", help="install the optional Teams cache parser (pinned)")
    p.add_argument("what", choices=["teams"])
    p.add_argument("--yes", action="store_true", help="actually run the commands (default: dry run)")
    p.set_defaults(fn=cmd_vendor_install)

    p = sp.add_parser("add-samples")
    p.add_argument("--register", required=True, choices=REGISTERS)
    p.add_argument("--file")
    p.add_argument("--text")
    p.add_argument("--recipient")
    p.set_defaults(fn=cmd_add_samples)

    p = sp.add_parser("build")
    p.add_argument("--no-mask", action="store_true", help="keep email addresses / phone numbers in the bank")
    p.set_defaults(fn=cmd_build)
    sp.add_parser("status").set_defaults(fn=cmd_status)

    p = sp.add_parser("kit")
    p.add_argument("--register", required=True, choices=REGISTERS)
    p.add_argument("--notes")
    p.add_argument("--notes-file")
    p.add_argument("--context")
    p.add_argument("--context-file")
    p.add_argument("--recipient")
    p.add_argument("--subject")
    p.add_argument("--k", type=int, default=3)
    p.set_defaults(fn=cmd_kit)

    for name, fn in (("critique-prompt", cmd_critique_prompt), ("critique", cmd_critique)):
        p = sp.add_parser(name)
        p.add_argument("--kit", required=True)
        p.add_argument("--draft-file")
        p.add_argument("--draft")
        p.add_argument("--model")
        p.set_defaults(fn=fn)

    p = sp.add_parser("lint")
    p.add_argument("--register", required=True, choices=REGISTERS)
    p.add_argument("--file")
    p.add_argument("--text")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_lint)

    p = sp.add_parser("log-draft")
    p.add_argument("--kit")
    p.add_argument("--register", choices=REGISTERS)
    p.add_argument("--recipient")
    p.add_argument("--file")
    p.add_argument("--text")
    p.set_defaults(fn=cmd_log_draft)

    p = sp.add_parser("learn")
    p.add_argument("action", choices=["diff", "add", "note", "rule"])
    p.add_argument("--register", required=True, choices=REGISTERS)
    p.add_argument("--draft-file")
    p.add_argument("--draft")
    p.add_argument("--sent-file")
    p.add_argument("--sent")
    p.add_argument("--recipient")
    p.add_argument("--context")
    p.add_argument("--context-file")
    p.add_argument("--text", action="append", help="note / rule line (repeatable)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_learn)

    p = sp.add_parser("check")
    p.add_argument("action", choices=["prepare", "generate", "page", "score"])
    p.add_argument("--register", default="chat", choices=REGISTERS)
    p.add_argument("--n", type=int, default=12)
    p.add_argument("--run")
    p.add_argument("--model")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--include-plain", action="store_true", help="also test plain-Claude pairs on the page")
    p.add_argument("--open", action="store_true")
    p.add_argument("--no-judge", action="store_true")
    p.add_argument("--picks", help="exported picks JSON from the blind-test page")
    p.add_argument("--demo", action="store_true",
                   help="label the run as a demo (automatic when the items are synthetic fixtures)")
    p.set_defaults(fn=cmd_check)

    a = ap.parse_args(argv)
    old_umask = os.umask(0o077)  # everything the CLI creates is owner-only (files 0600, dirs 0700)
    try:
        a.fn(a)
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
