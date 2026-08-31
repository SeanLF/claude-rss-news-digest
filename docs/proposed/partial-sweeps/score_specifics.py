"""Deterministic grounding scorer for a WRITE draft.

Primary metric: the rate of UNSUPPORTED SPECIFICS -- numbers, dates and proper nouns
that appear in the generated headline/summary/why_it_matters but nowhere in the text
the story is allowed to draw on. Reported against two corpora:

  CITED  the story's own `sources` article_ids (title + RSS summary + fulltext)
  POOL   every article in the run's CSVs (+ fulltext)

POOL-absent is the prior-knowledge fabrication signal (the run-280 defect: "Gulf Arab
states have moved to restrict or halt commerce with Iran" is in no article at all).

Matching is NFKD-folded, diacritic-stripped, casefolded, with TRUE word boundaries on
BOTH ends -- `(?<!\\w)secret(?!\\w)` must not match "Secretary", and "Mladic" must map
onto "Mladic~". Both traps are real and were hit before.

No model calls. Systematic scorer bias (e.g. a common word counted as a proper noun)
inflates the denominator identically in every arm, so it cancels in the between-arm
comparison; it is still reported so the absolute rate is read with the right caveat.
"""
from __future__ import annotations

import csv, json, re, sys, unicodedata
from pathlib import Path

# --------------------------------------------------------------------------- #
# normalisation
# --------------------------------------------------------------------------- #

_DASH = re.compile(r"[‐-―−]")
_WS = re.compile(r"\s+")


def fold(s: str) -> str:
    """NFKD-fold, drop combining marks, unify quotes/dashes, collapse whitespace."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = (s.replace("’", "'").replace("‘", "'")
           .replace("“", '"').replace("”", '"'))
    s = _DASH.sub("-", s)
    return _WS.sub(" ", s)


def present(term: str, corpus: str) -> bool:
    """True-word-boundary containment on both ends, case-insensitive."""
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", corpus, re.IGNORECASE) is not None


# --------------------------------------------------------------------------- #
# specific extraction
# --------------------------------------------------------------------------- #

CONNECTORS = {"of", "the", "and", "for", "de", "del", "da", "van", "von", "al",
              "el", "bin", "ibn", "du", "la", "le", "di", "in", "on"}

# Capitalised words that are ordinary English and carry no factual specificity.
# Excluding them keeps the DENOMINATOR meaningful; they are always present in any
# corpus so they never affect the numerator.
STOP = {w.lower() for w in """
A An The And But Or Nor For Yet So If When While After Before Since Until Because
In On At By From To Of With Without Within Into Onto Over Under Between Among Across
It Its This That These Those There Their They Them He She His Her Him We Us Our You Your I My
Is Are Was Were Be Been Being Has Have Had Do Does Did Will Would Shall Should Can Could May
Might Must Not No Now New Both Each Every All Any Some Most More Less Least Only Also Such
Than Then What Which Who Whom Whose Why How Where Here Still Even Just Yet Meanwhile However
Instead Rather Both Neither Either Another Other Others Many Few Several Much Own Same
Following Amid Despite Though Although Unlike Like About Against Through During Per Via
""".split()}

NUMWORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
            "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
            "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
            "eighty", "ninety", "hundred", "thousand", "million", "billion", "trillion",
            "dozen", "half", "third", "quarter", "double", "triple",
            "first", "second", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"}

_NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?!\w)")
_CAPTOK = re.compile(r"\b([A-Z][A-Za-z'\-]*)")
_SENT = re.compile(r"(?<=[.!?])\s+")


def _num_variants(n: str) -> list[str]:
    """A number may be written 1,200 / 1200 / 1 200 in the sources."""
    bare = n.replace(",", "")
    out = {n, bare}
    if len(bare) > 3 and "." not in bare:
        out.add(f"{int(bare):,}")
    return sorted(out)


def extract_specifics(text: str, known_caps: set[str] | None = None) -> list[dict]:
    """Return {kind, term, variants} for every checkable specific in `text`.

    kinds: num (digit string), numword (spelled quantity/ordinal),
           ent (single capitalised token), entphrase (multi-token capitalised run).

    `known_caps` (lowercased) is the set of tokens seen capitalised in a NON-sentence-
    initial position anywhere in this story. A sentence-initial capital that never
    appears mid-sentence is ambiguous ("Separately, Rwanda..." / "Charging him with...")
    and is dropped rather than scored as a proper noun.
    """
    t = fold(text)
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, term: str, variants: list[str] | None = None):
        key = (kind, term.lower())
        if key in seen or not term:
            return
        seen.add(key)
        out.append({"kind": kind, "term": term, "variants": variants or [term]})

    for m in _NUM.finditer(t):
        add("num", m.group(1), _num_variants(m.group(1)))

    for w in re.findall(r"(?<!\w)([a-z]+)(?!\w)", t.lower()):
        if w in NUMWORDS:
            add("numword", w)

    for sent in _SENT.split(t):
        toks = sent.split(" ")
        i, phrase = 0, []
        for j, raw in enumerate(toks):
            core = raw.strip(".,;:!?\"'()[]")
            is_cap = bool(core) and core[0].isupper() and re.fullmatch(r"[A-Za-z'\-]+", core or "x") is not None
            base = re.sub(r"'s$", "", core)
            low = base.lower()
            initial_only = j == 0 and known_caps is not None and low not in known_caps
            if is_cap and low not in STOP and not initial_only:
                # keep the ORIGINAL token in the phrase so "Iran's Supreme Leader" is
                # matched with its possessive, not as the never-written "Iran Supreme Leader"
                phrase.append(core)
                # hyphen split: "Israel-Hamas" -> Israel, Hamas
                for part in base.split("-"):
                    if len(part) > 1 and part[0].isupper() and part.lower() not in STOP:
                        add("ent", part)
            elif core.lower() in CONNECTORS and phrase and j + 1 < len(toks):
                nxt = toks[j + 1].strip(".,;:!?\"'()[]")
                if nxt[:1].isupper():
                    phrase.append(core)
                    continue
                if len(phrase) > 1:
                    add("entphrase", " ".join(phrase))
                phrase = []
            else:
                if len(phrase) > 1:
                    add("entphrase", " ".join(phrase))
                phrase = []
        if len(phrase) > 1:
            add("entphrase", " ".join(phrase))
        i += 1
    return out


def phrase_present(phrase: str, corpus: str) -> bool:
    """Contiguous phrase match, tolerant of a possessive on any token."""
    toks = [re.escape(re.sub(r"'s$", "", t)) + r"(?:'s)?" for t in phrase.split(" ")]
    return re.search(r"(?<!\w)" + r"\W+".join(toks) + r"(?!\w)", corpus, re.IGNORECASE) is not None


def check(spec: dict, corpus: str) -> bool:
    if spec["kind"] == "entphrase":
        return phrase_present(spec["term"], corpus)
    return any(present(v, corpus) for v in spec["variants"])


# --------------------------------------------------------------------------- #
# corpora
# --------------------------------------------------------------------------- #

def load_corpus(fixture: Path) -> tuple[dict[str, str], str]:
    """article_id -> folded text, and the folded whole-pool text."""
    per: dict[str, list[str]] = {}
    for csv_path in sorted(fixture.glob("articles_*.csv")):
        with csv_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                aid = (row.get("article_id") or "").strip()
                if aid:
                    per.setdefault(aid, []).extend(
                        [row.get("title") or "", row.get("summary") or ""])
    ft_path = fixture / "article_fulltext.json"
    if ft_path.exists():
        ft = json.loads(ft_path.read_text(encoding="utf-8"))
        for aid, val in (ft or {}).items():
            txt = val.get("text") if isinstance(val, dict) else val
            if isinstance(txt, str):
                per.setdefault(aid, []).append(txt)
    folded = {aid: fold(" \n ".join(parts)) for aid, parts in per.items()}
    return folded, fold(" \n ".join(" ".join(p) for p in per.values()))


def _noninitial_caps(text: str) -> set[str]:
    """Lowercased tokens seen capitalised somewhere OTHER than a sentence start."""
    out: set[str] = set()
    for sent in _SENT.split(fold(text)):
        for tok in sent.split(" ")[1:]:
            core = tok.strip(".,;:!?\"'()[]")
            if core[:1].isupper() and re.fullmatch(r"[A-Za-z'\-]+", core or "x"):
                out.add(re.sub(r"'s$", "", core).lower())
    return out


def story_texts(story: dict) -> dict[str, str]:
    return {f: (story.get(f) or "") for f in ("headline", "summary", "why_it_matters")}


def score_draft(draft: dict, per: dict[str, str], pool: str) -> dict:
    rows = []
    for tier in ("must_know", "should_know"):
        for story in draft.get(tier) or []:
            if not isinstance(story, dict):
                continue
            ids = [s.get("article_id") for s in (story.get("sources") or [])
                   if isinstance(s, dict) and s.get("article_id")]
            cited = fold(" \n ".join(per.get(a, "") for a in ids))
            texts = story_texts(story)
            caps = _noninitial_caps(" ".join(texts.values()))
            for field, text in texts.items():
                for spec in extract_specifics(text, caps):
                    rows.append({
                        "tier": tier, "field": field, "headline": story.get("headline"),
                        "kind": spec["kind"], "term": spec["term"],
                        "in_cited": check(spec, cited), "in_pool": check(spec, pool),
                        "n_cited": len(ids),
                    })
    return {"rows": rows, "stories": summarise_stories(draft)}


def summarise_stories(draft: dict) -> list[dict]:
    out = []
    for tier in ("must_know", "should_know"):
        for s in draft.get(tier) or []:
            if not isinstance(s, dict):
                continue
            out.append({
                "tier": tier, "headline": s.get("headline"),
                "headline_chars": len(s.get("headline") or ""),
                "summary_chars": len(s.get("summary") or ""),
                "summary_words": len((s.get("summary") or "").split()),
                "summary_sentences": len([x for x in _SENT.split(fold(s.get("summary") or "")) if x.strip()]),
                "why_chars": len(s.get("why_it_matters") or ""),
                "why_empty": not (s.get("why_it_matters") or "").strip(),
                "n_sources": len([x for x in (s.get("sources") or []) if isinstance(x, dict)]),
                "has_reporting_varies": bool(s.get("reporting_varies")),
            })
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--draft", required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    per, pool = load_corpus(Path(a.fixture))
    res = score_draft(json.loads(Path(a.draft).read_text()), per, pool)
    print(json.dumps({"label": a.label, **res}) if a.json else
          f"{a.label}: {sum(1 for r in res['rows'] if not r['in_pool'])}/{len(res['rows'])} pool-absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
