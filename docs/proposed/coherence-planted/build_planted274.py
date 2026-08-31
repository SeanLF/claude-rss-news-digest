"""Build held-out set 3 from archived run 274 -- an INDEPENDENT confirmation set.

Set 2 (run 278) put Opus 5 ahead of Sonnet 5 at n=5, t~2.6 -- marginal. This is a
second, independent held-out set from a DIFFERENT run, same taxonomy, same
known-bad/clean balance, so the two can be pooled.

Blind-scoring change vs set 2: the label key is written OUTSIDE the fixture
directory. Set 2 kept labels.json inside the dir mounted at /app/eval-fixtures,
which the agent has Read on -- a real (if unused) leakage channel. Here the
fixture dir contains only what production COHERENCE sees.

Every plant verified by word-boundary regex to be ABSENT from, or directly
contradicted by, that story's OWN cited sources (CSV summaries + article_fulltext).
"""
import json, shutil
from pathlib import Path

ROOT = Path("/Users/sean/Developer/news-digest/scratch/coherence-models")
SRC = ROOT / "build/run274_raw"
OUT = ROOT / "build/planted274"          # fixture only -- no labels
KEY = ROOT.parent / "coherence-keys/planted274_key.json"  # label key, never mounted into the container

PLANTS = [
    (6, "headline", "MBS arrives in Paris for strategic talks",
     "MBS arrives in Berlin for strategic talks", "EntE-wrong-entity",
     "Cited sources place the visit and the Esports World Cup closing ceremony in PARIS; 'Berlin' appears in no cited source, and it contradicts the story's own summary ('arrived in Paris on Sunday')."),
    (7, "why_it_matters", "Canada sends roughly 70 percent of its exports to the United States",
     "Canada sends nearly all of its exports to the United States", "quantifier-overstatement",
     "A cited source states '70% of its exports are to the US'. 'Nearly all' contradicts the source quantifier."),
    (4, "summary", "killing at least 30 people, with 22 others injured",
     "killing at least 30 people, with 74 others injured", "numeric-contradiction",
     "Cited sources state 22 injured (six seriously, 16 lightly); 74 appears in no cited source."),
    (15, "why_it_matters", "The mission plan requires a 90-day lunar-orbit reconnaissance before landing",
     "After a July launch-pad fire destroyed ground support equipment, the mission plan still requires a 90-day lunar-orbit reconnaissance before landing", "LinkE-fabricated-event",
     "No cited source mentions any fire or launch-pad damage ('fire' and 'launch pad' are absent from the cited set; sources attribute the delay to a safety assessment and Typhoon Narra)."),
    (5, "summary", "authorities imposed a curfew in Narathiwat",
     "Deputy Prime Minister Phumtham Wechayachai imposed a curfew in Narathiwat", "attribution-upgrade",
     "Cited sources say 'Authorities imposed an overnight curfew'; the name appears nowhere in the corpus."),
    (14, "summary", "according to Business Insider.",
     "according to Business Insider, with the round led by Sequoia Capital and existing backers Google and Amazon expected to exit their stakes.", "OutE-padding",
     "Sole source is a one-line headline with no fulltext; the investors and the exit are invented (none appear in the cited set)."),
    (12, "summary", "coalition partner New Zealand First said it would not support it",
     "coalition partner New Zealand First, which holds eight seats, said it would not support it", "OutE-absence",
     "No cited source states New Zealand First's seat count."),
    (8, "why_it_matters", "is eroding the visible deterrent",
     "is eroding a visible deterrent the Biden administration had worked to reinforce, and is eroding the visible deterrent", "stale-world-state",
     "Cited sources describe the Trump administration; 'Biden' appears in no cited source."),
]

CLEAN_IDX = [0, 1, 2, 3, 9, 10, 13, 16]  # entirely untouched stories
EXCLUDED = [11]                           # production COHERENCE flagged it -> not a trustworthy negative
FIELDS = ("headline", "summary", "why_it_matters")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    KEY.parent.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*"):
        f.unlink()
    shutil.copy(SRC / "article_fulltext.json", OUT / "article_fulltext.json")
    for p in SRC.glob("articles_*.csv"):
        shutil.copy(p, OUT / p.name)

    ds = json.loads((SRC / "draft_selections.json").read_text())
    stories = ds["must_know"] + ds["should_know"]
    hard = []
    for idx, field, old, new, typ, why in PLANTS:
        cur = stories[idx][field]
        if old not in cur:
            raise SystemExit(f"plant idx={idx} {field}: anchor not found:\n  {old!r}\n  in {cur!r}")
        stories[idx][field] = cur.replace(old, new, 1)
        hard.append({"idx": idx, "field": field, "type": typ, "claim": new, "why": why})
    (OUT / "draft_selections.json").write_text(json.dumps(ds, indent=2))

    key = {
        "_doc": ("Held-out set 3, built from archived run 274. 8 fabrications planted into 8 "
                 "distinct stories, one field each, every one verified absent from or contradicted "
                 "by that story's OWN cited sources. clean_fields = the 3 fields of 8 entirely "
                 "UNTOUCHED stories. CAVEAT (same as set 2): 'untouched' means production COHERENCE "
                 "passed it and nothing was planted -- NOT a hand-audited clean label. Flags on "
                 "clean fields must be adjudicated against the sources before being called false "
                 "positives. idx 11 excluded: production flagged it on the real run."),
        "hard_positives": hard,
        "borderline": [],
        "clean_fields": [{"idx": i, "field": f} for i in CLEAN_IDX for f in FIELDS],
        "excluded_idx": EXCLUDED,
        "idx_headlines": {str(i): s["headline"] for i, s in enumerate(stories)},
    }
    KEY.write_text(json.dumps(key, indent=2))
    print(f"fixture -> {OUT}  ({len(list(OUT.glob('*')))} files, NO labels inside)")
    print(f"key     -> {KEY}  ({len(hard)} known-bad, {len(key['clean_fields'])} clean)")
    for h in hard:
        print(f"   idx {h['idx']:>2} {h['field']:<15} {h['type']}")


if __name__ == "__main__":
    main()
