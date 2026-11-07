"""Build the voice pack from extracted sources.

pack/
  sources/<source>.jsonl  raw extracted records (local only)
  exemplars.jsonl         cleaned, de-duplicated bank (what retrieval reads)
  stats.json              per-register stylometric profile + fixed exemplar ids
  rules.md                frequency-calibrated rules (editable; KEEP BELOW section survives rebuilds)
  profile.md              human-readable summary
  learned.md              notes from voice-learn (never overwritten)
"""
import os
import re
from collections import Counter

from . import __version__
from .common import REGISTERS, now_iso, private_open, read_json, read_jsonl, secure_tree, write_json, write_jsonl
from .rules import build_profile_md, build_rules_md, choose_fixed, split_keep
from .stylo import features
from .textutil import mask_pii, mask_recipient, normalise, word_count

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


def build(pack, mask=True, k_fixed=6):
    recs, per_source = load_sources(pack)
    bank, dropped = clean_bank(recs, mask=mask)
    bank.sort(key=lambda r: (r["register"], r.get("timestamp") or "", r["id"]))
    write_jsonl(os.path.join(pack, "exemplars.jsonl"), bank)
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
            "fixed_ids": choose_fixed(rr, k=k_fixed, max_words=max(8, F.get("len_p90", 40) * 1.15),
                                      mean_words=F.get("len_mean")),
        }
    stats = {"built": built, "version": __version__, "masked_pii": mask, "registers": regs,
             "dropped": dict(dropped), "sources": dict(per_source)}
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
        f.write(build_profile_md(all_feats, dict(Counter(r["source"] for r in bank)), built))
    lp = os.path.join(pack, "learned.md")
    if not os.path.exists(lp):
        with private_open(lp) as f:
            f.write(LEARNED_HEADER)
    secure_tree(pack)
    return stats


def summary(stats):
    """Counts only (no text), safe to show in the terminal."""
    L = ["built %s" % stats.get("built")]
    for reg, d in stats.get("registers", {}).items():
        F = d["features"]
        L.append("  %-6s %5d messages from %s; median %s words; imperfection %s; %d fixed exemplars"
                 % (reg, d["n"], ", ".join("%s=%d" % kv for kv in d["sources"].items()),
                    int(round(F.get("len_median", 0))), F.get("imperfection_level"), len(d["fixed_ids"])))
    if stats.get("dropped"):
        L.append("  dropped: " + ", ".join("%s=%d" % kv for kv in sorted(stats["dropped"].items())))
    return "\n".join(L)


def load_pack(pack):
    stats = read_json(os.path.join(pack, "stats.json"), {}) or {}
    bank = read_jsonl(os.path.join(pack, "exemplars.jsonl"))
    rules = ""
    rp = os.path.join(pack, "rules.md")
    if os.path.exists(rp):
        with open(rp, encoding="utf-8") as f:
            rules = f.read()
    return stats, bank, rules
