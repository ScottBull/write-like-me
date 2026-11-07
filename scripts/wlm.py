#!/usr/bin/env python3
"""write-like-me command line. Python 3.9+ standard library only. Run as:

    python3 -I "$PLUGIN/scripts/wlm.py" <command> [options]

Commands (all data stays in the voice pack: $WRITE_LIKE_ME_PACK or ~/.claude/voice-pack):
  discover                         which sources exist on this machine (metadata only)
  extract <source> [...]           claude_code | teams | outlook | files PATH | imessage
  vendor-install teams [--yes]     install the optional Teams parser at pinned commits (dry run first)
  add-samples --register R --file F   pasted samples, separated by lines of ---
  build                            clean + measure + write archive/, profile.md, rules.md, stats.json
  status                           what's in the pack (counts only), its format version and defaults
  context --register R --notes ... select archive entries within the budget; write the draft's
                                   system file and task file
  draft --system-file F --task-file T   one draft via `claude -p` (fallback when no subagent)
  lint --register R --file F       deterministic checks against the hard rules
  log-draft --register R --file F  remember a final draft (caricature tracking, voice-learn)
  learn diff|add|note|rule         learn from what you actually sent
  person build|show|add|forget     the person file (what you know and think, cited)
  check prepare|generate|page|score   blind test (pairs + lineups) and scoring
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir

from wlmlib import build as B  # noqa: E402
from wlmlib import check, cli_context, cli_person  # noqa: E402
from wlmlib import learn, lint as LN  # noqa: E402
from wlmlib.common import (REGISTERS, append_jsonl, ensure_pack, now_iso, pack_dir, private_open, read_json,  # noqa: E402
                           read_jsonl, short_hash, write_jsonl)
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


def cmd_draft(a):
    from wlmlib import draft
    _need_pack()
    task = _read(a.task_file, None)
    if not task.strip():
        sys.exit("the task file is empty")
    try:
        print(draft.draft(a.system_file, task, model=a.model))
    except RuntimeError as e:
        sys.exit("draft failed: %s" % e)


def _private_if_in_pack(pack, path):
    """A draft file the model saved inside the pack (the Write tool uses default modes): make it 0600."""
    if path and os.path.abspath(os.path.expanduser(path)).startswith(os.path.abspath(pack) + os.sep):
        try:
            os.chmod(os.path.expanduser(path), 0o600)
        except OSError:
            pass


def cmd_lint(a):
    pack = _need_pack()
    stats, _, rules = B.load_pack(pack)
    _private_if_in_pack(pack, a.file)
    text = _read(a.file, a.text)
    recent = read_jsonl(os.path.join(pack, "drafts.jsonl"))
    res = LN.lint(text, a.register, rules, stats, recent)
    print(json.dumps(res, indent=1) if a.json else LN.format_report(res))


def cmd_log_draft(a):
    pack = _need_pack()
    ctx = {}
    if a.context_dir:
        ctx = read_json(os.path.join(a.context_dir, "context.json"))
        if not ctx:
            sys.exit("no context.json in %s" % a.context_dir)
    register = a.register or ctx.get("register")
    if not register:
        sys.exit("give --register (or --context-dir)")
    _private_if_in_pack(pack, a.file)
    text = _read(a.file, a.text).strip()
    if not text:
        sys.exit("nothing to log")
    row = {"at": now_iso(), "register": register, "recipient": a.recipient or ctx.get("recipient", ""),
           "text": text, "context_dir": a.context_dir or ""}
    append_jsonl(os.path.join(pack, "drafts.jsonl"), row)
    if a.context_dir:
        with private_open(os.path.join(a.context_dir, "final.txt")) as f:
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

    p = sp.add_parser("build", help="clean + measure the sources and write archive/, stats.json, rules.md, "
                                    "profile.md (upgrades a v1 pack)")
    p.add_argument("--no-mask", action="store_true", help="keep email addresses / phone numbers in archive/")
    p.set_defaults(fn=cmd_build)
    sp.add_parser("status", help="what's in the pack (counts only), format version, defaults").set_defaults(fn=cmd_status)

    cli_context.add_parser(sp)

    p = sp.add_parser("draft", help="one draft via claude -p: the system file as the system prompt, the task on stdin")
    p.add_argument("--system-file", required=True, help="system.txt from `wlm context`")
    p.add_argument("--task-file", required=True, help="task.txt from `wlm context`")
    p.add_argument("--model", help="model (default: $WRITE_LIKE_ME_MODEL, else your default)")
    p.set_defaults(fn=cmd_draft)

    p = sp.add_parser("lint")
    p.add_argument("--register", required=True, choices=REGISTERS)
    p.add_argument("--file")
    p.add_argument("--text")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_lint)

    p = sp.add_parser("log-draft")
    p.add_argument("--context-dir", help="the `wlm context` directory (register and recipient from its context.json)")
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

    cli_person.add_parser(sp)
    check.add_parser(sp)

    a = ap.parse_args(argv)
    old_umask = os.umask(0o077)  # everything the CLI creates is owner-only (files 0600, dirs 0700)
    try:
        a.fn(a)
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    main()
