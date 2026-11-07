"""Build the voice pack (format v2) from extracted sources.

pack/
  sources/<source>.jsonl    raw extracted records (local only; the ground truth)
  archive/<register>.jsonl  cleaned, masked, de-duplicated messages in date order (archive.py)
  stats.json                pack_version, per-register stylometric profile, archive sizes, defaults
  rules.md                  hard rules only (editable; KEEP BELOW section survives rebuilds)
  profile.md                human-readable summary, including the habit tendencies
  learned.md                notes from voice-learn (never overwritten)
  drafts.jsonl              drafts logged by write-as-me (never touched by build)

Upgrading: a v1 pack (no pack_version in stats.json; exemplars.jsonl and fixed_ids) is rebuilt in
place from sources/: archive/ is written, exemplars.jsonl deleted, rules.md regenerated with its
KEEP section, learned.md and drafts.jsonl left alone.
"""
import os
import re
from collections import Counter

from . import __version__
from .archive import has_archive, load_archive, write_archive
from .common import (PACK_VERSION, REGISTERS, now_iso, pack_version, private_open, read_json, read_jsonl,
                     secure_tree, write_json)
from .rules import build_profile_md, build_rules_md, caricature_habits, spelling_prefs, split_keep
from .stylo import features
from .textutil import mask_pii, mask_recipient, normalise, word_count

# The draft defaults: the one place they are set. `build` writes them into stats.json as
# `budget_default` / `strategy_default`, and everything else (`wlm context`, voice-check) reads
# stats.json, falling back to these only for a pack built before they existed (`defaults()`).
# Provisional, from the context-budget ablation so far (compact strategy C at 10k tokens was the best
# cell); the final values come from its write-up.
BUDGET_DEFAULT = 10000     # tokens of archive + person file + learned notes + habits block per draft
STRATEGY_DEFAULT = "C"     # selection strategy (select.py)
PERSON_DEFAULT = "none"    # person file tier sent by default; the ablation found it hurt at every budget
# The "How my messages vary" block (habits.py) after the archive: `on` or `off`. Like PERSON_DEFAULT it
# is not written into stats.json; a pack overrides it with `habits_default` there. Off, from the final
# head-to-head (reviews/rebuild-results.md in the research workspace): with the block on, Teams drafts
# were spotted more often in the lineup and were easier for LUAR to tell apart, and they still never
# dropped a final period; on Reddit it lowered every bundle score.
HABITS_DEFAULT = "off"
HABITS_CHOICES = ("on", "off")

V1_FILES = ("exemplars.jsonl", "exemplars.jsonl.tmp")   # derived files a v2 pack no longer has

# Records with any of these flags are kept in sources/ but left out of the bank and stats:
# they are probably not the person's own prose.
EXCLUDE_FLAGS = {"possible_inline_paste", "possible_relayed_ai_text", "partial_ai_quote", "transcript_only"}
LEARNED_HEADER = """# Learned notes

Notes from comparing drafts with what you actually sent (`/write-like-me:voice-learn`). The
drafting skill reads this file after rules.md. Edit or delete freely; rebuilds never touch it.
"""


def _dedupe_key(text):
    return re.sub(r"\W+", " ", normalise(text).lower()).strip()


def clean_bank(records, mask=True, min_words=1, max_words=600):
    """Filter, de-duplicate and (optionally) mask the merged records. Returns (bank, dropped Counter)."""
    seen, bank, dropped = set(), [], Counter()
    for r in records:
        if not isinstance(r, dict):
            dropped["malformed"] += 1
            continue
        flags = set(r.get("flags") or [])
        bad = flags & EXCLUDE_FLAGS
        if bad:
            dropped["flag:" + sorted(bad)[0]] += 1
            continue
        if r.get("register") not in REGISTERS:
            dropped["unknown_register"] += 1
            continue
        text = (r.get("text") or "").strip()
        if mask:
            text = mask_pii(text)
        wc = word_count(text)
        if wc < min_words:
            dropped["empty"] += 1
            continue
        if wc > max_words:
            dropped["too_long"] += 1
            continue
        key = (r["register"], _dedupe_key(text))
        if key in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(key)
        rec = dict(r)
        rec["text"] = text
        rec["word_count"] = wc
        rec["notes"] = sorted(flags)
        rec["flags"] = []
        if mask and rec.get("context"):
            rec["context"] = mask_pii(rec["context"])
        if mask and rec.get("recipient"):
            rec["recipient"] = mask_recipient(rec["recipient"])
        if mask and rec.get("subject"):              # email subjects are shown in drafting prompts
            rec["subject"] = mask_pii(rec["subject"])
        if mask and rec.get("thread") and rec.get("register") == "email":   # email thread = subject
            rec["thread"] = mask_pii(rec["thread"])
        bank.append(rec)
    return bank, dropped


def load_sources(pack):
    src_dir = os.path.join(pack, "sources")
    recs, per_source = [], Counter()
    if os.path.isdir(src_dir):
        for f in sorted(os.listdir(src_dir)):
            if f.endswith(".jsonl"):
                rows = read_jsonl(os.path.join(src_dir, f))
                recs += rows
                per_source[f[:-6]] += len(rows)
    return recs, per_source


def defaults(stats):
    """(budget, strategy) for a draft from a pack's stats.json, else the constants above."""
    s = stats or {}
    return int(s.get("budget_default") or BUDGET_DEFAULT), str(s.get("strategy_default") or STRATEGY_DEFAULT).upper()


def habits_default(stats, given=None):
    """'on' or 'off': `given` (a --habits flag) if set, else stats.json `habits_default`, else
    HABITS_DEFAULT. A value that is not on/off raises ValueError."""
    v = given
    if v is None or v == "":
        v = (stats or {}).get("habits_default")
    if v is None or v == "":
        v = HABITS_DEFAULT
    v = ("on" if v else "off") if isinstance(v, bool) else str(v).strip().lower()
    if v not in HABITS_CHOICES:
        raise ValueError("habits must be on or off, not %r (check habits_default in stats.json)" % v)
    return v


def _carry_defaults(old_stats, budget_default=None, strategy_default=None):
    """The defaults a rebuild writes, and which of them came from the constants (`defaults_auto`).

    A value passed in, or one in the old stats.json that differs from what the build last wrote by
    itself (the user edited it), is the user's and is kept. A value the build wrote from the
    constants follows the constants, so a plugin update that changes them reaches the pack on the
    next build."""
    auto_old = old_stats.get("defaults_auto") or {}
    out, auto = {}, {}
    for key, given, const in (("budget_default", budget_default, BUDGET_DEFAULT),
                              ("strategy_default", strategy_default, STRATEGY_DEFAULT)):
        old = old_stats.get(key)
        if given:
            out[key] = given
        elif old and (key not in auto_old or old != auto_old[key]) and not _pre_auto(old_stats, key, old):
            out[key] = old
        else:
            out[key] = auto[key] = const
    out["budget_default"] = int(out["budget_default"])
    out["strategy_default"] = str(out["strategy_default"]).upper()
    return out, auto


# What 0.2.0 development builds wrote before `defaults_auto` existed: treated as written by the build.
_PRE_AUTO = {"budget_default": 50000, "strategy_default": "D"}


def _pre_auto(old_stats, key, value):
    return "defaults_auto" not in old_stats and _PRE_AUTO.get(key) == value


def build(pack, mask=True, budget_default=None, strategy_default=None):
    """Clean + measure + write archive/, stats.json, rules.md, profile.md (and learned.md if missing).
    Upgrades a v1 pack. Budget and strategy defaults the user set (passed here, or edited into
    stats.json) are kept; the ones the build set itself follow BUDGET_DEFAULT / STRATEGY_DEFAULT."""
    old_stats = read_json(os.path.join(pack, "stats.json"), {}) or {}
    old_version = pack_version(old_stats)
    recs, per_source = load_sources(pack)
    bank, dropped = clean_bank(recs, mask=mask)
    bank.sort(key=lambda r: (r["register"], r.get("timestamp") or "", r["id"]))
    archive = write_archive(pack, bank)
    for f in V1_FILES:
        fp = os.path.join(pack, f)
        if os.path.exists(fp):
            os.remove(fp)
    built = now_iso()
    regs = {}
    all_feats = {}
    for reg in REGISTERS:
        rr = [r for r in bank if r["register"] == reg]
        if not rr:
            continue
        F = features([r["text"] for r in rr])
        all_feats[reg] = F
        regs[reg] = {
            "n": len(rr),
            "sources": dict(Counter(r["source"] for r in rr)),
            "recipients": len({r.get("recipient") for r in rr if r.get("recipient")}),
            "features": F,
            "caricature_habits": caricature_habits(F),
            "spelling_prefs": spelling_prefs(F),
        }
    stats = {"pack_version": PACK_VERSION, "built": built, "version": __version__, "masked_pii": mask,
             "registers": regs, "archive": archive,
             "dropped": dict(dropped), "sources": dict(per_source)}
    dflt, auto = _carry_defaults(old_stats, budget_default, strategy_default)
    stats.update(dflt)
    stats["defaults_auto"] = auto
    if old_version is not None and old_version < PACK_VERSION:
        stats["upgraded_from"] = old_version
    write_json(os.path.join(pack, "stats.json"), stats)

    rules_p = os.path.join(pack, "rules.md")
    keep = ""
    if os.path.exists(rules_p):
        with open(rules_p, encoding="utf-8") as f:
            old = f.read()
        _, keep = split_keep(old)
        with private_open(os.path.join(pack, "rules.prev.md")) as f:
            f.write(old)
    with private_open(rules_p) as f:
        f.write(build_rules_md(all_feats, built, keep))
    with private_open(os.path.join(pack, "profile.md")) as f:
        f.write(build_profile_md(all_feats, dict(Counter(r["source"] for r in bank)), built, archive))
    lp = os.path.join(pack, "learned.md")
    if not os.path.exists(lp):
        with private_open(lp) as f:
            f.write(LEARNED_HEADER)
    secure_tree(pack)
    return stats


def needs_upgrade(stats):
    v = pack_version(stats)
    return v is not None and v < PACK_VERSION


def summary(stats):
    """Counts only (no text), safe to show in the terminal."""
    v = pack_version(stats)
    L = ["built %s; pack format v%s" % (stats.get("built"), v)]
    if needs_upgrade(stats):
        L.append("  this pack uses an old format: run `wlm build` to upgrade (sources/ are kept, nothing is lost)")
    elif stats.get("upgraded_from"):
        L.append("  upgraded from pack format v%s" % stats["upgraded_from"])
    arch = stats.get("archive") or {}
    for reg, d in stats.get("registers", {}).items():
        F = d["features"]
        a = arch.get(reg)
        tail = ("; archive %d entries, ~%d tokens" % (a["entries"], a["tokens"])) if a else ""
        L.append("  %-6s %5d messages from %s; median %s words; imperfection %s%s"
                 % (reg, d["n"], ", ".join("%s=%d" % kv for kv in d["sources"].items()),
                    int(round(F.get("len_median", 0))), F.get("imperfection_level"), tail))
    if v and v >= 2:
        try:
            h = habits_default(stats)
        except ValueError:
            h = "invalid (%r)" % stats.get("habits_default")
        L.append("  defaults: budget %s tokens, strategy %s, habits block %s"
                 % (stats.get("budget_default"), stats.get("strategy_default"), h))
    if stats.get("dropped"):
        L.append("  dropped: " + ", ".join("%s=%d" % kv for kv in sorted(stats["dropped"].items())))
    return "\n".join(L)


def load_pack(pack):
    """(stats, bank, rules text). The bank is the archive (all registers); a v1 pack not yet rebuilt
    falls back to its exemplars.jsonl."""
    stats = read_json(os.path.join(pack, "stats.json"), {}) or {}
    if has_archive(pack):
        bank = load_archive(pack)
    else:
        bank = read_jsonl(os.path.join(pack, "exemplars.jsonl"))
    rules = ""
    rp = os.path.join(pack, "rules.md")
    if os.path.exists(rp):
        with open(rp, encoding="utf-8") as f:
            rules = f.read()
    return stats, bank, rules
