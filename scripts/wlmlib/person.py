"""The person file: a cited description of what the person knows, thinks and does, distilled from
their own archive. It carries the judgment layer the archive shows only by example; it never
describes style (the archive does that).

Two files, because work and personal data have different owners; each is built only on request:
  person.md        from the personal registers: posts and comments, iMessage, and (only when the
                   user says they are personal) docs and chat from `extract files`, pasted samples
                   and `learn add`
  person.work.md   from the work sources: Teams, Outlook, Claude Code prompts
Beside them:
  person.citations.json   the sidecar: every claim with the archive entry ids behind it, every kept
                          sentence with the claims it rests on, and the claims the user stated
  person.blacklist.json   claims the user removed (`wlm person forget`); a rebuild drops any claim
                          that matches one, so it never comes back

Pipeline (`build`), each stage a separate function:
  select_entries  archive entries -> {file kind: {label: entries in date order}}
  chunk           entries -> ~40k-token windows
  map_chunk       one call per window -> claims {kind, claim, confidence, years, ids}
                  (parse_claims keeps only ids that are entries of that window)
  merge_deterministic   same kind + normalised text -> one claim, union of ids
  reduce_kind     one call per kind -> near-duplicates merged, change over time noted
  apply_citation_rule   two distinct entry ids or out; EXCEPTION: a biographical fact stated
                        plainly once (kind fact, confidence high) survives, marked single_source
  blacklist_filter      drop what the user forgot
  write_tier      draft + up to two tightening calls per tier (core ~3k tokens, extension ~7k)
  check           deterministic: every sentence must end in a [cN] marker naming a surviving
                  claim; a sentence without one is dropped, not fixed; markers go to the sidecar

File format (read by cli_context.person_tier):
  # About me                      (person.work.md: "# About me at work")
  <!-- header comment -->
  <!-- tier: core -->
  ...core sections...
  ## Stated by me                 (claims from `wlm person add`, one bullet each)
  <!-- tier: full -->
  ...extension sections...
The core tier is everything above `<!-- tier: full -->` (also accepted: `<!-- tier:full -->`); the
full tier is the whole file; a file with no marker is all core. HTML comments are never sent.

Model calls go through llm.call (claude -p on the person's own login, ANTHROPIC_API_KEY stripped by
child_env), cached under <pack>/work/person-cache so a failed build resumes cheaply.
"""
import collections
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from . import llm
from .archive import load_archive
from .cli_context import TIER_MARK, person_tier
from .common import CHARS_PER_TOKEN, private_open, read_json, write_json

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(HERE)), "templates")

FILES = {"personal": "person.md", "work": "person.work.md"}
TITLES = {"personal": "# About me", "work": "# About me at work"}
SIDECAR = "person.citations.json"
BLACKLIST = "person.blacklist.json"
CACHE_DIR = os.path.join("work", "person-cache")
SIDECAR_VERSION = 1

WORK_SOURCES = ("teams", "outlook", "claude_code")
PERSONAL_REGISTERS = ("post", "chat", "doc")
# A `wlm learn add` chat reply may have gone to a work chat: left out of both files.
AMBIGUOUS = {("learned", "chat")}
# Chat and docs from these sources may be work material (a folder of work documents, pasted Slack
# messages): left out of both files unless the user says they are personal (`include_unsorted`,
# `wlm person build --include-unsorted`). Posts and comments (register `post`) are public and personal
# whatever their source.
UNSORTED_SOURCES = ("file", "pasted", "learned")

WORKERS = 3
CHUNK_TOKENS = 40000
CORE_TOKENS = 3000
EXT_TOKENS = 7000            # full tier = core + extension, about 10k tokens
TOLERANCE = 0.10
TIGHTEN_PASSES = 2
TEXT_CAP = {"prompt": 1200}  # chars kept per entry (long prompts are pasted specs and code)
TEXT_CAP_DEFAULT = 4000
CONTEXT_CAP = 300
SIMILAR = 0.85               # difflib ratio at which a rebuilt claim counts as a forgotten one

KINDS = ["fact", "stance", "knowledge", "habit-of-argument", "humour", "relationship", "reference"]
CONF_RANK = {"high": 3, "medium": 2, "low": 1}
USER_HEADING = "## Stated by me"
USER_CITED = "user-stated"

SOURCE_NOTES = {
    "post": "These entries are the person's own public posts and comments (forums such as Reddit), with "
            "the post or comment each replied to where known.",
    "chat": "These entries are the person's own personal chat messages (texts and chats with friends and "
            "family), with the message before each where known.",
    "doc": "These entries are passages from documents the person wrote.",
    "teams": "These entries are the person's own messages in their employer's work chat, with the "
             "message before each where known. Keep every project, client and product at a generic level.",
    "outlook": "These entries are emails the person sent at work. Keep every project, client and product "
               "at a generic level.",
    "claude_code": "These entries are the person's own typed prompts to an AI coding assistant (Claude "
                   "Code). The project a prompt belongs to is deliberately not shown; where a prompt "
                   "names a client, brand or project, generalise. What matters is what they know, what "
                   "they ask for, what they correct, what they refuse, how they treat the assistant.",
}
KIND_NOTES = {
    "fact": "Biographical facts. Keep every distinct fact; merge only true repeats. A fact with one "
            "supporting entry is allowed to survive.",
    "stance": "Opinions. Keep distinct positions separate; mark changes over time in evolution.",
    "knowledge": "Domains they know and things they do. Merge by domain, keeping the depth.",
    "habit-of-argument": "How they reason and argue. Merge by mechanism.",
    "humour": "What they joke about and how the joke works. Merge by mechanism or target.",
    "relationship": "How they treat people. Merge by the kind of person and the behaviour.",
    "reference": "Things they return to. Merge by the thing referred to.",
}
FRAMES = {
    "personal": "The claims describe the person in their personal life, from their own posts, personal "
                "chats and documents. Describe them for a model that will draft their personal messages, "
                "posts and comments in their voice.",
    "work": "The claims describe the person at work, from their own work chat, work email and prompts to "
            "an AI coding assistant. Describe them for a model that will draft their work messages, "
            "emails and prompts to an AI assistant in their voice.",
}
SECTIONS = {
    "personal": {
        "core": ["## Who they are", "## What they know and do", "## Stances", "## How they argue",
                 "## Humour", "## How they treat people"],
        "full": ["## Opinions by topic", "## Recurring references", "## More on how they argue"],
    },
    "work": {
        "core": ["## Role and what they work on", "## How they work", "## How they work with an AI",
                 "## Stances on tools and craft", "## How they treat the team", "## Humour at work"],
        "full": ["## Opinions by topic", "## Recurring references and tools",
                 "## More on how they direct work"],
    },
}
HEADER = ("<!-- write-like-me person file ({kind}), built {date} by `wlm person build`. Citations are in "
          "person.citations.json. A rebuild rewrites this file: add a claim with `wlm person add` and "
          "remove one with `wlm person forget`, which both survive rebuilds; hand edits do not. -->")


# ---------------------------------------------------------------- helpers

def tokens(text):
    return int(round(len(text or "") / CHARS_PER_TOKEN))


def today():
    return dt.date.today().isoformat()


def normalise(text):
    t = re.sub(r"[^\w ]+", " ", (text or "").lower().replace("_", " "))
    return " ".join(t.split())


def claim_key(kind, text):
    """Stable across rebuilds for the same kind and normalised wording (cN numbers are not)."""
    return hashlib.sha1(("%s|%s" % (kind or "", normalise(text))).encode("utf-8")).hexdigest()[:12]


def _fill(tmpl, **values):
    """One-pass {key} replacement: substituted text (real messages) is never searched again."""
    return re.sub(r"\{(%s)\}" % "|".join(map(re.escape, values)),
                  lambda m: str(values[m.group(1)]), tmpl)


def template(name):
    return llm.split_template(os.path.join(TEMPLATES, "person_%s.md" % name))


def _write_text(path, text):
    tmp = path + ".tmp"
    with private_open(tmp) as f:
        f.write(text)
    os.replace(tmp, path)


def _unfence(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[^\n]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    return t.strip()


def parse_json(text, want):
    """Model output -> JSON array (want=list) or object (want=dict). Tolerates a code fence or a
    line of prose around it. Raises ValueError."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        d = json.loads(t)
    except ValueError:
        o, c = ("[", "]") if want is list else ("{", "}")
        i, j = t.find(o), t.rfind(c)
        if i < 0 or j <= i:
            raise ValueError("no JSON %s in model output" % want.__name__)
        d = json.loads(t[i:j + 1])
    if not isinstance(d, want):
        raise ValueError("expected a JSON %s" % want.__name__)
    return d


class Caller:
    """llm.call with a ledger (made / cached per stage) and one retry on unparseable output, which
    evicts the bad reply from the cache first."""

    def __init__(self, cache_dir=None, model=None):
        self.cache_dir, self.model = cache_dir, model
        self.counts = collections.Counter()
        self._lock = threading.Lock()

    def _cache_path(self, system, user):
        if not self.cache_dir:
            return None
        model = self.model or os.environ.get("WRITE_LIKE_ME_MODEL") or ""
        key = hashlib.sha256(json.dumps([model, system, user]).encode("utf-8")).hexdigest()[:24]
        return os.path.join(self.cache_dir, key + ".json")

    def __call__(self, stage, system, user, parse=None):
        cp = self._cache_path(system, user)
        for _ in range(2):
            cached = bool(cp and os.path.exists(cp))
            text = llm.call(system, user, cache_dir=self.cache_dir, model=self.model, retries=2, timeout=1200)
            with self._lock:
                self.counts["%s_%s" % (stage, "cached" if cached else "made")] += 1
            if parse is None:
                return text
            try:
                return parse(text)
            except ValueError:
                if cp and os.path.exists(cp):
                    os.remove(cp)
        raise RuntimeError("person %s: the model's output did not parse twice" % stage)


# ---------------------------------------------------------------- 1. select

def classify(entry, include_unsorted=False):
    """'work', 'personal' or None for one archive entry. Chat and docs from UNSORTED_SOURCES are
    personal only with `include_unsorted` (the user said they are), else None."""
    src, reg = entry.get("source") or "", entry.get("register") or ""
    if src in WORK_SOURCES:
        return "work"
    if (src, reg) in AMBIGUOUS:
        return None
    if reg == "post":
        return "personal"
    if src in UNSORTED_SOURCES and reg in PERSONAL_REGISTERS:
        return "personal" if include_unsorted else None
    if reg in PERSONAL_REGISTERS:
        return "personal"
    return None


def is_unsorted(entry):
    """True for an entry the personal build leaves out unless told it is personal."""
    return classify(entry) is None and classify(entry, include_unsorted=True) == "personal"


def _label(entry, kind):
    return entry.get("source") if kind == "work" else entry.get("register")


def to_item(e):
    """An archive entry as a map-prompt item (no recipient names: third parties stay out)."""
    reg = e.get("register") or ""
    text = (e.get("text") or "").strip()
    cap = TEXT_CAP.get(reg, TEXT_CAP_DEFAULT)
    if len(text) > cap:
        text = text[:cap].rsplit(" ", 1)[0] + " [...]"
    if reg == "post":
        setting = (e.get("recipient") or "").strip() or "post"
    elif e.get("source") == "teams":
        setting = "work chat"
    else:
        setting = {"chat": "chat", "email": "email", "doc": "document", "prompt": ""}.get(reg, reg)
    return {"id": e.get("id") or "", "date": (e.get("timestamp") or "")[:10] or "undated",
            "setting": setting, "context": (e.get("context") or "").strip(),
            "context_label": "after" if reg in ("chat", "email") else "replying to", "text": text}


def select_entries(pack, include_unsorted=False):
    """{'personal': {label: [items]}, 'work': {label: [items]}}, date order within each label."""
    out = {"personal": collections.OrderedDict(), "work": collections.OrderedDict()}
    rows = sorted(load_archive(pack), key=lambda e: (e.get("timestamp") or "", e.get("id") or ""))
    for e in rows:
        kind = classify(e, include_unsorted)
        if kind and (e.get("text") or "").strip() and e.get("id"):
            out[kind].setdefault(_label(e, kind), []).append(to_item(e))
    return out


# ---------------------------------------------------------------- 2. chunk

def render_item(it):
    head = ("[%s] %s %s" % (it["id"], it["date"], it["setting"])).rstrip()
    lines = [head]
    if it["context"]:
        lines.append('  %s: "%s"' % (it["context_label"], it["context"].replace("\n", " ")[:CONTEXT_CAP]))
    lines.append(it["text"])
    return "\n".join(lines)


def chunk(items, chunk_tokens=CHUNK_TOKENS):
    """Greedy windows over items in the given (date) order, each about chunk_tokens of rendered text."""
    limit = int(chunk_tokens * CHARS_PER_TOKEN)
    chunks, cur, size = [], [], 0
    for it in items:
        n = len(render_item(it)) + 2
        if cur and size + n > limit:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += n
    if cur:
        chunks.append(cur)
    return chunks


def count_unsorted(pack):
    """{source: n} of the archive entries the personal build leaves out unless told they are personal."""
    return dict(collections.Counter(e.get("source") or "" for e in load_archive(pack)
                                    if (e.get("text") or "").strip() and e.get("id") and is_unsorted(e)))


def estimate(pack, kinds=("personal", "work"), chunk_tokens=CHUNK_TOKENS, include_unsorted=False):
    """{kind: {entries: {label: n}, chunks: n, calls_min, calls_max}} without any model call."""
    sel = select_entries(pack, include_unsorted)
    out = {}
    for k in kinds:
        groups = sel[k]
        n_chunks = sum(len(chunk(v, chunk_tokens)) for v in groups.values())
        out[k] = {"file": FILES[k], "entries": {lab: len(v) for lab, v in groups.items()},
                  "entries_total": sum(len(v) for v in groups.values()), "chunks": n_chunks,
                  "reduce_max": len(KINDS), "write_min": 2, "write_max": 2 * (1 + TIGHTEN_PASSES)}
        out[k]["calls_min"] = n_chunks + 2 if n_chunks else 0
        out[k]["calls_max"] = n_chunks + len(KINDS) + 2 * (1 + TIGHTEN_PASSES) if n_chunks else 0
    return out


# ---------------------------------------------------------------- 3. map

def resolve_id(raw, valid_ids):
    """A cited id back to an entry id of the window, or None. Models drop a source prefix
    ('teams:') or clip characters off long ids: exact first, then with a prefix seen in the window,
    then a unique prefix or suffix match of at least ten characters."""
    r = str(raw).strip().strip("[]").strip()
    if not r:
        return None
    if r in valid_ids:
        return r
    for p in sorted({v.split(":", 1)[0] + ":" for v in valid_ids if ":" in v}):
        if p + r in valid_ids:
            return p + r
    if len(r) >= 10:
        hits = [v for v in valid_ids if v.startswith(r) or v.endswith(r)]
        if len(hits) == 1:
            return hits[0]
    return None


def parse_claims(parsed, valid_ids, label=""):
    """The map step's JSON array -> (claims, dropped counter). A claim keeps only ids that resolve
    to entries of its window; one with no valid id, an unknown kind or no text is dropped."""
    claims, dropped = [], collections.Counter()
    if not isinstance(parsed, list):
        raise ValueError("map output is not a JSON array")
    for c in parsed:
        if not isinstance(c, dict):
            dropped["not_an_object"] += 1
            continue
        kind = str(c.get("kind", "")).strip().lower()
        text = " ".join(str(c.get("claim", "")).split())
        if kind not in KINDS or not text:
            dropped["bad_kind_or_empty"] += 1
            continue
        raw = c.get("ids") or []
        if not isinstance(raw, list):
            raw = [raw]
        ids = list(dict.fromkeys(filter(None, (resolve_id(i, valid_ids) for i in raw))))
        dropped["ids_unresolved"] += sum(1 for i in raw if not resolve_id(i, valid_ids))
        if not ids:
            dropped["no_valid_ids"] += 1
            continue
        conf = str(c.get("confidence", "medium")).strip().lower()
        claims.append({"kind": kind, "claim": text, "ids": ids, "source": label,
                       "confidence": conf if conf in CONF_RANK else "medium",
                       "years": str(c.get("years") or "")})
    return claims, dropped


def map_chunk(call, items, label, chunk_no, chunk_total):
    system, user_t = template("map")
    user = _fill(user_t, source_note=SOURCE_NOTES.get(label, SOURCE_NOTES["doc"]), chunk_no=chunk_no,
                 chunk_total=chunk_total, entry_count=len(items),
                 entries="\n\n".join(render_item(it) for it in items))
    valid = {it["id"] for it in items}
    return call("map", system, user, parse=lambda t: parse_claims(parse_json(t, list), valid, label))


def run_map(call, groups, workers=WORKERS, chunk_tokens=CHUNK_TOKENS):
    jobs = []
    for label, items in groups.items():
        chs = chunk(items, chunk_tokens)
        jobs += [(ch, label, i, len(chs)) for i, ch in enumerate(chs, 1)]
    claims, dropped = [], collections.Counter()
    with ThreadPoolExecutor(max(1, workers)) as pool:
        for cl, dr in pool.map(lambda j: map_chunk(call, *j), jobs):
            claims.extend(cl)
            dropped.update(dr)
    return claims, dropped


# ---------------------------------------------------------------- 4. reduce

def years_span(claims):
    ys = []
    for c in claims:
        ys += [int(y) for y in re.findall(r"\b(19\d\d|20\d\d)\b", c.get("years") or "")]
    return "%d-%d" % (min(ys), max(ys)) if ys else ""


def _best_conf(parts):
    return max((p.get("confidence", "medium") for p in parts), key=lambda x: CONF_RANK.get(x, 0))


def merge_deterministic(claims):
    """Same kind + same normalised text -> one claim with the union of ids."""
    groups = collections.OrderedDict()
    for c in claims:
        k = (c["kind"], normalise(c["claim"]))
        if k in groups:
            g = groups[k]
            g["ids"] = list(dict.fromkeys(g["ids"] + c["ids"]))
            g["years"] = years_span([g, c])
            g["confidence"] = _best_conf([g, c])
            g["sources"] = sorted(set(g["sources"]) | {c.get("source", "")} - {""})
        else:
            groups[k] = dict(c, ids=list(c["ids"]), sources=[c["source"]] if c.get("source") else [])
    return list(groups.values())


def parse_reduce(d, claims, kind):
    """The reduce step's JSON object -> (claims, dropped). Ids are the deterministic union of the
    inputs each output names in `from`; an output naming no valid input is dropped."""
    out, dropped, used = [], collections.Counter(), set()
    for m in d.get("claims") or []:
        if not isinstance(m, dict):
            continue
        src = [int(i) for i in (m.get("from") or []) if str(i).strip().isdigit() and 1 <= int(i) <= len(claims)]
        text = " ".join(str(m.get("claim", "")).split())
        if not src or not text:
            dropped["reduce_no_sources"] += 1
            continue
        used.update(src)
        parts = [claims[i - 1] for i in src]
        out.append({"kind": kind, "claim": text,
                    "ids": list(dict.fromkeys(i for p in parts for i in p["ids"])),
                    "confidence": _best_conf(parts), "years": years_span(parts),
                    "sources": sorted({s for p in parts for s in p.get("sources", [])}),
                    "evolution": (m.get("evolution") or None)})
    for dr in d.get("dropped") or []:
        if isinstance(dr, dict):
            dropped["reduce_dropped_" + str(dr.get("reason", "other")).replace(" ", "_")] += 1
    dropped["reduce_unreferenced_inputs"] += len(claims) - len(used)
    return out, dropped


def reduce_kind(call, kind, claims):
    """One call per kind (none for zero or one claim): near-duplicates merged, evolution noted."""
    if len(claims) <= 1:
        return [dict(c, evolution=c.get("evolution")) for c in claims], collections.Counter()
    system, user_t = template("reduce")
    lines = ["%d. [%s; %s; %d entries] %s" % (i, c["confidence"], c["years"] or "n/a", len(c["ids"]), c["claim"])
             for i, c in enumerate(claims, 1)]
    user = _fill(user_t, kind=kind, kind_note=KIND_NOTES[kind], claim_count=len(claims), claims="\n".join(lines))
    return call("reduce", system, user, parse=lambda t: parse_reduce(parse_json(t, dict), claims, kind))


def run_reduce(call, claims, workers=WORKERS):
    by_kind = collections.defaultdict(list)
    for c in claims:
        by_kind[c["kind"]].append(c)
    out, dropped = [], collections.Counter()
    with ThreadPoolExecutor(max(1, workers)) as pool:
        for cl, dr in pool.map(lambda k: reduce_kind(call, k, by_kind.get(k, [])), KINDS):
            out.extend(cl)
            dropped.update(dr)
    return out, dropped


def apply_citation_rule(claims):
    """Two distinct entry ids or out.
    BIOGRAPHY EXCEPTION: a fact (kind `fact`, confidence `high`) with one id survives, marked
    `single_source` (a biographical fact the person stated plainly once)."""
    kept, dropped = [], collections.Counter()
    for c in claims:
        n = len(set(c["ids"]))
        if n >= 2:
            kept.append(dict(c, single_source=False))
        elif n == 1 and c["kind"] == "fact" and c.get("confidence") == "high":
            kept.append(dict(c, single_source=True))
        else:
            dropped[c["kind"]] += 1
    return kept, dropped


def assign_ids(claims):
    """c1..cN in kind order, best supported first, plus the stable `key`."""
    order = {k: i for i, k in enumerate(KINDS)}
    claims = sorted(claims, key=lambda c: (order.get(c["kind"], 99), -len(c["ids"])))
    for i, c in enumerate(claims, 1):
        c["cid"] = "c%d" % i
        c["key"] = claim_key(c["kind"], c["claim"])
    return claims


# ---------------------------------------------------------------- blacklist

def load_blacklist(pack):
    """{"version": 1, "claims": [{key, kind, text, file, id, forgotten}], "terms": [str]}.
    `terms` (edited by hand) drops any claim containing one of the words or phrases."""
    d = read_json(os.path.join(pack, BLACKLIST), None) or {}
    return {"version": 1, "claims": [c for c in d.get("claims") or [] if isinstance(c, dict)],
            "terms": [str(t) for t in d.get("terms") or [] if str(t).strip()]}


def save_blacklist(pack, bl):
    write_json(os.path.join(pack, BLACKLIST), bl)


def is_blacklisted(claim_text, kind, bl):
    n = normalise(claim_text)
    if not n:
        return False
    padded = " %s " % n
    for t in bl.get("terms") or []:
        nt = normalise(t)
        if nt and " %s " % nt in padded:
            return True
    key = claim_key(kind, claim_text)
    for b in bl.get("claims") or []:
        if b.get("key") == key:
            return True
        bt = b.get("text") or ""
        if not bt:
            continue
        if bt == n or difflib.SequenceMatcher(None, bt, n).ratio() >= SIMILAR:
            return True
    return False


def blacklist_filter(claims, bl):
    kept = [c for c in claims if not is_blacklisted(c["claim"], c["kind"], bl)]
    return kept, len(claims) - len(kept)


# ---------------------------------------------------------------- 5. write

def claim_line(c, used=None):
    flags = []
    if c.get("single_source"):
        flags.append("single source")
    if used and c["cid"] in used:
        flags.append("used in core")
    s = "%s [%s; %d entries; %s] %s" % (c["cid"], c["kind"], len(c["ids"]), c.get("years") or "n/a", c["claim"])
    if c.get("evolution"):
        s += " evolution: %s" % c["evolution"]
    if flags:
        s += " (%s)" % "; ".join(flags)
    return s


def draft_core(call, kind, claims):
    system_t, user_t = template("write_core")
    system = _fill(system_t, target_tokens=CORE_TOKENS, target_chars=int(CORE_TOKENS * CHARS_PER_TOKEN),
                   sections="\n".join(SECTIONS[kind]["core"]))
    user = _fill(user_t, frame=FRAMES[kind], claim_count=len(claims),
                 claims="\n".join(claim_line(c) for c in claims))
    return _unfence(call("write", system, user))


def draft_full(call, kind, claims, core_marked):
    used = set(cited_ids(core_marked))
    system_t, user_t = template("write_full")
    system = _fill(system_t, target_tokens=EXT_TOKENS, target_chars=int(EXT_TOKENS * CHARS_PER_TOKEN),
                   sections="\n".join(SECTIONS[kind]["full"]))
    user = _fill(user_t, frame=FRAMES[kind], core=core_marked, claim_count=len(claims),
                 claims="\n".join(claim_line(c, used) for c in claims))
    return _unfence(call("write", system, user))


def tighten(call, text, target, claims, max_passes=TIGHTEN_PASSES):
    """Up to max_passes revision calls toward target tokens (within TOLERANCE); the claims offered
    for expansion are those the text does not cite. Returns (text, draft_tokens, passes)."""
    draft_tokens = measured = tokens(strip_markers(text))
    passes = 0
    while passes < max_passes and abs(measured - target) > target * TOLERANCE:
        delta = abs(target - measured)
        n_sent = max(1, int(round(delta / 25.0)))
        if measured > target:
            direction = "Cut about %d tokens: remove roughly %d sentences and stop there." % (delta, n_sent)
        else:
            direction = ("Add about %d tokens, roughly %d sentences of twenty to thirty words from the extra "
                         "claims below, and stop there; do not expand existing sentences." % (delta, n_sent))
        used = set(cited_ids(text))
        extra = [c for c in claims if c["cid"] not in used]
        system_t, user_t = template("tighten")
        system = _fill(system_t, measured_tokens=measured, target_tokens=target, direction=direction)
        user = _fill(user_t, text=text, extra_count=len(extra),
                     extra="\n".join(claim_line(c) for c in extra) or "(none)")
        text = _unfence(call("write", system, user))
        measured = tokens(strip_markers(text))
        passes += 1
    return text, draft_tokens, passes


def write_tier(call, kind, claims, tier, core_marked=None):
    """tier 'core' -> the core draft; 'full' -> the extension (appended after the core)."""
    if tier == "core":
        text = draft_core(call, kind, claims)
        text, before, passes = tighten(call, text, CORE_TOKENS, claims)
    else:
        text = draft_full(call, kind, claims, core_marked or "")
        text, before, passes = tighten(call, text, EXT_TOKENS, claims)
    return text, {"draft_tokens": before, "tighten_passes": passes}


# ---------------------------------------------------------------- 6. check (deterministic)

MARKER = re.compile(r"\[\s*(c\d+(?:\s*,\s*c\d+)*)\s*\]")
_BOUNDARY = re.compile("[.!?][\"')”’]*\\s+(?=[\"'(“‘]?[A-Z0-9])")
_ABBREV = {"mr", "mrs", "ms", "dr", "st", "vs", "etc", "approx", "no", "jr", "sr", "inc", "ltd", "co"}
_LIST_PREFIX = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)?(.*)$", re.S)
_HEADING = re.compile(r"^(#{1,6})\s")
_COMMENT_LINE = re.compile(r"^\s*<!--.*-->\s*$")


def cited_ids(text):
    out = []
    for m in MARKER.finditer(text or ""):
        out += [x.strip() for x in m.group(1).split(",")]
    return out


def strip_markers(text):
    t = MARKER.sub("", text or "")
    t = re.sub(r"[ \t]+([.,;:!?])", r"\1", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return re.sub(r"[ \t]+\n", "\n", t).strip()


def _has_words(s):
    return bool(re.search(r"\w", s or ""))


def split_sentences(seg):
    """Split prose at sentence ends (., ! or ? then space then a capital, digit or opening quote),
    not after abbreviations such as 'e.g.', 'U.S.' or 'Dr.'."""
    out, start = [], 0
    for m in _BOUNDARY.finditer(seg):
        prev = re.search(r"(\S+)$", seg[start:m.start() + 1])
        word = prev.group(1) if prev else ""
        if re.fullmatch(r"(?:[A-Za-z]\.)+", word) or word.lower().rstrip(".") in _ABBREV:
            continue
        out.append(seg[start:m.end()])
        start = m.end()
    out.append(seg[start:])
    return [s for s in out if s.strip()]


def _check_body(body, by_cid, stats):
    """One line's prose -> (kept clean sentences, kept marked sentences, records)."""
    clean, marked, recs, pos = [], [], [], 0
    for m in MARKER.finditer(body):
        punct = re.match(r"[.!?]*", body[m.end():]).group(0)
        end = m.end() + len(punct)
        cids = [x.strip() for x in m.group(1).split(",")]
        parts = split_sentences(body[pos:m.start()])
        pos = end
        for u in parts[:-1]:
            if _has_words(u):
                stats["dropped_unmarked"] += 1
        cited = (parts[-1] if parts else "").strip()
        if not _has_words(cited):
            # "[c4][c9]." or "... [c4]. [c9]": a bare marker joins the sentence before it
            if recs and all(c in by_cid for c in cids):
                recs[-1]["claims"] = list(dict.fromkeys(recs[-1]["claims"] + cids))
                recs[-1]["ids"] = list(dict.fromkeys(recs[-1]["ids"] + [i for c in cids for i in by_cid[c]["ids"]]))
                end_p = punct if punct and not re.search(r"[.!?]$", clean[-1]) else ""
                clean[-1] += end_p
                recs[-1]["sentence"] = clean[-1]
                marked[-1] = re.sub(r"\[[^\]]*\]([.!?]*)$", lambda mm: "[%s]%s" % (", ".join(recs[-1]["claims"]),
                                                                          mm.group(1) + end_p), marked[-1])
            continue
        if any(c not in by_cid for c in cids):
            stats["dropped_unknown_marker"] += 1
            continue
        cited = cited.rstrip()
        sent = strip_markers(cited + punct)
        ids = list(dict.fromkeys(i for c in cids for i in by_cid[c]["ids"]))
        recs.append({"sentence": sent, "claims": cids, "ids": ids})
        clean.append(sent)
        marked.append("%s [%s]%s" % (strip_markers(cited), ", ".join(cids), punct))
        stats["kept"] += 1
    for u in split_sentences(body[pos:]):
        if _has_words(u):
            stats["dropped_unmarked"] += 1
    return clean, marked, recs


def prune_empty_headings(text):
    """Drop a heading (below level 1) with no content before the next heading of the same or a
    higher level. Comment lines (tier markers) are kept and do not count as content."""
    lines = text.split("\n")
    keep = [True] * len(lines)
    for i, line in enumerate(lines):
        h = _HEADING.match(line)
        if not h or len(h.group(1)) == 1:
            continue
        level, empty = len(h.group(1)), True
        for nxt in lines[i + 1:]:
            if not nxt.strip() or _COMMENT_LINE.match(nxt):
                continue
            h2 = _HEADING.match(nxt)
            empty = bool(h2) and len(h2.group(1)) <= level
            break
        if empty:
            keep[i] = False
    out = "\n".join(l for l, k in zip(lines, keep) if k)
    return re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"


TOPIC_HEADING_WORDS = 6      # a model-chosen `###` topic heading longer than this is a claim, not a topic


def check(text, claims, headings=None):
    """Every sentence must end in a marker naming surviving claims, or it is deleted (not fixed).
    Headings carry no citation, so with `headings` (the expected section headings) a `#`/`##`
    heading not in that list, and a deeper (topic) heading over TOPIC_HEADING_WORDS words, is
    deleted too: a model could otherwise state an uncited claim as a heading.
    Returns (clean_text, records, stats, marked_text): clean has no markers; records are
    [{sentence, claims, ids}] in order; marked keeps the markers of the kept sentences."""
    allowed = None if headings is None else {" ".join(h.split()) for h in headings}
    by_cid = {c["cid"]: c for c in claims}
    clean_lines, marked_lines, records, stats = [], [], [], collections.Counter()
    for line in (text or "").split("\n"):
        s = line.rstrip()
        if not s.strip():
            clean_lines.append("")
            marked_lines.append("")
            continue
        hm = _HEADING.match(s.lstrip())
        if hm:
            h = strip_markers(s.strip())
            if allowed is not None and (" ".join(h.split()) not in allowed if len(hm.group(1)) <= 2
                                        else len(h.lstrip("#").split()) > TOPIC_HEADING_WORDS):
                stats["dropped_heading"] += 1
                continue
            clean_lines.append(h)
            marked_lines.append(h)
            continue
        if _COMMENT_LINE.match(s):
            continue
        m = _LIST_PREFIX.match(s)
        prefix, body = (m.group(1) or ""), m.group(2)
        clean, marked, recs = _check_body(body, by_cid, stats)
        records += recs
        if clean:
            clean_lines.append(prefix + " ".join(clean))
            marked_lines.append(prefix + " ".join(marked))
    return (prune_empty_headings("\n".join(clean_lines)), records, stats,
            prune_empty_headings("\n".join(marked_lines)))


# ---------------------------------------------------------------- sidecar + file rendering

def load_sidecar(pack):
    d = read_json(os.path.join(pack, SIDECAR), None) or {}
    files = d.get("files") if isinstance(d.get("files"), dict) else {}
    return {"version": SIDECAR_VERSION, "files": files}


def save_sidecar(pack, sc):
    write_json(os.path.join(pack, SIDECAR), sc)


def file_entry(sc, name):
    e = sc["files"].setdefault(name, {})
    e.setdefault("claims", {})
    e.setdefault("sentences", {"core": [], "extension": []})
    e.setdefault("user_claims", {})
    return e


def _clean_user_text(text):
    """One line of plain text: no comments (they could fake a tier marker), no citation markers."""
    t = re.sub(r"<!--.*?-->", " ", text or "", flags=re.S).replace("<!--", " ").replace("-->", " ")
    t = strip_markers(" ".join(t.split()))
    return re.sub(r"^[-*+#>\s]+", "", t).strip()


def render_file(kind, core_clean, ext_clean, user_claims, built=None):
    """The person file text in the settled format (see the module docstring)."""
    parts = [TITLES[kind], "", HEADER.format(kind=kind, date=built or today()), "", "<!-- tier: core -->"]
    if (core_clean or "").strip():
        parts += [core_clean.strip(), ""]
    users = [u["claim"] for u in user_claims.values()] if user_claims else []
    if users:
        parts += [USER_HEADING] + ["- " + u for u in users] + [""]
    if (ext_clean or "").strip():
        parts += ["<!-- tier: full -->", ext_clean.strip(), ""]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip() + "\n"


def person_path(pack, kind):
    return os.path.join(pack, FILES[kind])


# ---------------------------------------------------------------- 7. build

def build(pack, kind, model=None, workers=WORKERS, log=None, chunk_tokens=CHUNK_TOKENS, include_unsorted=False):
    """Build one person file (kind 'personal' or 'work'). Returns stats (counts only), or None when
    the pack has no entries for it. The file and the sidecar are written only at the end.
    `include_unsorted`: the user said the chat and docs from UNSORTED_SOURCES are personal."""
    log = log or (lambda s: None)
    groups = select_entries(pack, include_unsorted)[kind]
    if not groups:
        return None
    call = Caller(os.path.join(pack, CACHE_DIR), model)
    bl = load_blacklist(pack)
    stats = {"file": FILES[kind], "entries": {k: len(v) for k, v in groups.items()}}
    stats["chunks"] = sum(len(chunk(v, chunk_tokens)) for v in groups.values())

    log("%s: map over %d chunks" % (FILES[kind], stats["chunks"]))
    mapped, map_dropped = run_map(call, groups, workers, chunk_tokens)
    stats["map_claims"] = len(mapped)
    stats["map_dropped"] = dict(map_dropped)
    mapped, bl_map = blacklist_filter(mapped, bl)

    merged = merge_deterministic(mapped)
    stats["after_merge"] = len(merged)
    log("%s: reduce %d claims" % (FILES[kind], len(merged)))
    reduced, red_dropped = run_reduce(call, merged, workers)
    stats["after_reduce"] = len(reduced)
    stats["reduce_dropped"] = dict(red_dropped)

    kept, cit_dropped = apply_citation_rule(reduced)
    stats["dropped_for_citations"] = sum(cit_dropped.values())
    stats["dropped_for_citations_by_kind"] = dict(cit_dropped)
    kept, bl_red = blacklist_filter(kept, bl)
    stats["dropped_by_blacklist"] = bl_map + bl_red
    claims = assign_ids(kept)
    stats["claims"] = len(claims)
    stats["claims_by_kind"] = dict(collections.Counter(c["kind"] for c in claims))
    stats["single_source_facts"] = sum(1 for c in claims if c["single_source"])

    core_clean = ext_clean = ""
    core_recs, ext_recs = [], []
    if claims:
        log("%s: write the core and full tiers from %d claims" % (FILES[kind], len(claims)))
        core_raw, core_info = write_tier(call, kind, claims, "core")
        core_clean, core_recs, core_chk, core_marked = check(core_raw, claims, headings=SECTIONS[kind]["core"])
        ext_raw, ext_info = write_tier(call, kind, claims, "full", core_marked=core_marked)
        ext_clean, ext_recs, ext_chk, _ = check(ext_raw, claims, headings=SECTIONS[kind]["full"])
        stats["write"] = {"core": core_info, "extension": ext_info}
        stats["check"] = {"core": dict(core_chk), "extension": dict(ext_chk)}
    stats["tokens"] = {"core": tokens(core_clean), "extension": tokens(ext_clean),
                       "full": tokens(core_clean) + tokens(ext_clean)}

    sc = load_sidecar(pack)
    old = sc["files"].get(FILES[kind]) or {}
    users = dict(old.get("user_claims") or {})      # stated by the user: kept across rebuilds
    stats["user_claims"] = len(users)
    stats["calls"] = dict(call.counts)
    built = today()
    sc["files"][FILES[kind]] = {
        "built": built, "model": model or os.environ.get("WRITE_LIKE_ME_MODEL") or "default",
        "claims": {c["cid"]: {k: c.get(k) for k in ("key", "kind", "claim", "ids", "confidence", "years",
                                                     "single_source", "evolution", "sources")} for c in claims},
        "sentences": {"core": core_recs, "extension": ext_recs},
        "user_claims": users, "stats": stats,
    }
    _write_text(person_path(pack, kind), render_file(kind, core_clean, ext_clean, users, built))
    save_sidecar(pack, sc)
    return stats


# ---------------------------------------------------------------- add / forget / show

def _insert_user_line(text, line):
    """Insert a bullet at the end of the '## Stated by me' section, creating the section just above
    the full-tier marker (or at the end) when it is missing."""
    lines = text.rstrip("\n").split("\n")
    idx = next((i for i, l in enumerate(lines) if l.strip() == USER_HEADING), None)
    if idx is not None:
        last = idx
        for j in range(idx + 1, len(lines)):
            h = _HEADING.match(lines[j])
            if (h and len(h.group(1)) <= 2) or TIER_MARK.search(lines[j]):
                break
            if lines[j].strip():
                last = j
        lines.insert(last + 1, line)
    else:
        at = next((i for i, l in enumerate(lines) if TIER_MARK.search(l)), len(lines))
        block = [USER_HEADING, line, ""]
        if at > 0 and lines[at - 1].strip():
            block = [""] + block
        lines[at:at] = block
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def add(pack, text, kind="personal"):
    """Add a claim the user states, cited as user-stated, to the core tier. Returns its id (uN).
    An explicit add overrides an earlier forget of the same claim."""
    claim = _clean_user_text(text)
    if not _has_words(claim):
        raise ValueError("the claim is empty")
    sc = load_sidecar(pack)
    fe = file_entry(sc, FILES[kind])
    n = normalise(claim)
    for uid, u in fe["user_claims"].items():
        if normalise(u.get("claim")) == n:
            return uid, False
    bl = load_blacklist(pack)
    before = len(bl["claims"])
    bl["claims"] = [b for b in bl["claims"] if not (b.get("text") == n or b.get("key") == claim_key(None, claim))]
    if len(bl["claims"]) != before:
        save_blacklist(pack, bl)
    nums = [int(k[1:]) for k in fe["user_claims"] if re.fullmatch(r"u\d+", k)]
    uid = "u%d" % (max(nums) + 1 if nums else 1)
    fe["user_claims"][uid] = {"claim": claim, "added": today(), "cited": USER_CITED,
                              "key": claim_key(None, claim)}
    p = person_path(pack, kind)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            cur = f.read()
        new = _insert_user_line(cur, "- " + claim)
    else:
        new = render_file(kind, "", "", {uid: fe["user_claims"][uid]})
    _write_text(p, new)
    save_sidecar(pack, sc)
    return uid, True


def _resolve_forget(fe, target):
    """(claim ids, user ids, how) for a forget target, or ([], [], None) when nothing matches."""
    t = (target or "").strip()
    claims, users = fe.get("claims") or {}, fe.get("user_claims") or {}
    if t in claims:
        return [t], [], "id"
    if t in users:
        return [], [t], "id"
    for cid, c in claims.items():
        if c.get("key") == t:
            return [cid], [], "key"
    n = normalise(t)
    if not n:
        return [], [], None
    hits = [cid for cid, c in claims.items() if normalise(c.get("claim")) == n]
    uhits = [uid for uid, u in users.items() if normalise(u.get("claim")) == n]
    if hits or uhits:
        return hits, uhits, "claim text"
    recs = [r for tier in ("core", "extension") for r in (fe.get("sentences") or {}).get(tier, [])]
    for r in recs:
        if normalise(r.get("sentence")) == n:
            return list(r["claims"]), [], "sentence"
    best, score = None, 0.0
    cands = [("c", cid, c.get("claim")) for cid, c in claims.items()]
    cands += [("u", uid, u.get("claim")) for uid, u in users.items()]
    cands += [("s", i, r.get("sentence")) for i, r in enumerate(recs)]
    for typ, ref, txt in cands:
        r = difflib.SequenceMatcher(None, normalise(txt), n).ratio()
        if r > score:
            best, score = (typ, ref), r
    if best and score >= SIMILAR:
        typ, ref = best
        if typ == "c":
            return [ref], [], "close match"
        if typ == "u":
            return [], [ref], "close match"
        return list(recs[ref]["claims"]), [], "close match"
    return [], [], None


def _remove_sentences(text, sentences):
    for s in sentences:
        if not s:
            continue
        i = text.find(s)
        if i < 0:
            continue
        j = i + len(s)
        if j < len(text) and text[j] == " ":
            j += 1
        elif i > 0 and text[i - 1] == " ":
            i -= 1
        text = text[:i] + text[j:]
    lines = [l for l in text.split("\n") if not re.fullmatch(r"\s*(?:[-*+]|\d+[.)])\s*", l)]
    return prune_empty_headings("\n".join(lines))


def forget(pack, target, kind="personal"):
    """Delete a claim (cN, uN, its key, its claim text, or the text of a sentence) from the person
    file and the sidecar, and blacklist it so a rebuild never brings it back. Text that matches
    nothing current is blacklisted as given. Returns a summary dict."""
    sc = load_sidecar(pack)
    name = FILES[kind]
    fe = sc["files"].get(name) or {"claims": {}, "sentences": {}, "user_claims": {}}
    cids, uids, how = _resolve_forget(fe, target)
    if not cids and not uids and re.fullmatch(r"\s*[cu]\d+\s*", target or ""):
        raise ValueError("%s has no claim %s (see `wlm person show --ids`)" % (name, target.strip()))
    bl = load_blacklist(pack)
    keys = {b.get("key") for b in bl["claims"]}
    entries = []
    for cid in cids:
        c = fe["claims"].get(cid) or {}
        entries.append({"key": c.get("key") or claim_key(c.get("kind"), c.get("claim")), "kind": c.get("kind"),
                        "text": normalise(c.get("claim")), "file": name, "id": cid})
    for uid in uids:
        u = fe["user_claims"].get(uid) or {}
        entries.append({"key": u.get("key") or claim_key(None, u.get("claim")), "kind": None,
                        "text": normalise(u.get("claim")), "file": name, "id": uid})
    if not entries:
        n = normalise(target)
        if not n:
            raise ValueError("give a claim id or the claim's text")
        entries.append({"key": claim_key(None, target), "kind": None, "text": n, "file": name, "id": None})
        how = "text only"
    for e in entries:
        e["forgotten"] = today()
        if e["key"] not in keys:
            bl["claims"].append(e)
            keys.add(e["key"])
    save_blacklist(pack, bl)

    # sentences resting on a forgotten claim go, from the file and the sidecar
    gone = set(cids)
    removed = []
    for tier in ("core", "extension"):
        recs = (fe.get("sentences") or {}).get(tier, [])
        removed += [r["sentence"] for r in recs if gone & set(r["claims"])]
        if name in sc["files"]:
            fe["sentences"][tier] = [r for r in recs if not gone & set(r["claims"])]
    user_lines = []
    for uid in uids:
        user_lines.append("- " + fe["user_claims"][uid]["claim"])
        del fe["user_claims"][uid]
    for cid in cids:
        fe["claims"].pop(cid, None)
    p = person_path(pack, kind)
    if os.path.exists(p) and (removed or user_lines):
        with open(p, encoding="utf-8") as f:
            text = f.read()
        if user_lines:
            text = "\n".join(l for l in text.split("\n") if l.rstrip() not in user_lines)
        _write_text(p, _remove_sentences(text, removed))
    if name in sc["files"]:
        save_sidecar(pack, sc)
    return {"file": name, "matched_by": how, "claims": cids + uids, "sentences_removed": len(removed),
            "user_claims_removed": len(uids), "blacklisted": len(entries)}


def read_tier(pack, kind, tier="core"):
    """The tier text of one person file exactly as `wlm context` sends it ('' if absent)."""
    p = person_path(pack, kind)
    if not os.path.isfile(p):
        return ""
    with open(p, encoding="utf-8") as f:
        return person_tier(f.read(), tier)


def show_ids(pack, kind, tier="core"):
    """Lines '[c3, c9] sentence' for the tier's cited sentences and '[u1] claim (stated by you)' for
    user claims, from the sidecar; [] when the file was not built here."""
    fe = load_sidecar(pack)["files"].get(FILES[kind])
    if not fe:
        return []
    tiers = ["core"] if tier == "core" else ["core", "extension"]
    out = []
    for t in tiers:
        for r in (fe.get("sentences") or {}).get(t, []):
            out.append("[%s] %s" % (", ".join(r["claims"]), r["sentence"]))
    for uid, u in (fe.get("user_claims") or {}).items():
        out.append("[%s] %s (stated by you)" % (uid, u["claim"]))
    return out
