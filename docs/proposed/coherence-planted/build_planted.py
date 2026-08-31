"""Build the HELD-OUT planted-fabrication fixture from archived run 278.

Run 245 (the existing coherence_faithful fixture) is the set the adversarial
prompt was DESIGNED from -- its recall is training-set performance. This builds
an out-of-sample set from a healthy archived run: 8 fabrications planted into 8
distinct stories (one field each, mirroring the run-245 FRANK/RAGTruth taxonomy),
with 8 stories left untouched as the false-positive denominator.

Every plant was verified (chk pass) to be ABSENT from -- or directly contradicted
by -- that story's OWN cited sources.
"""
import json, shutil
from pathlib import Path

SRC = Path("/Users/sean/Developer/news-digest/scratch/coherence-models/build/run278_raw")
OUT = Path("/Users/sean/Developer/news-digest/scratch/coherence-models/build/planted278")

# (idx, field, old_substring, new_substring, type, why)
PLANTS = [
    (0, "headline",
     "Nepal-Tibet rescue", "Nepal-Bhutan rescue",
     "EntE-wrong-entity",
     "Cited sources say Nepal-Tibet border; 'Bhutan' appears in no cited source, and it contradicts the story's own summary."),
    (2, "summary",
     "at least 8,000 Muslims", "at least 12,000 Muslims",
     "numeric-contradiction",
     "Cited sources state 8,000; 12,000 appears nowhere."),
    (13, "summary",
     "delay or cancel about 20 percent of data centre projects",
     "delay or cancel most data centre projects",
     "quantifier-overstatement",
     "Cited source states 'about 20 percent'; 'most' contradicts the source quantifier."),
    (6, "why_it_matters",
     "meaning the two efforts compete for doses",
     "and renewed armed clashes in North Kivu have already forced three vaccination sites to suspend work, meaning the two efforts compete for doses",
     "LinkE-fabricated-event",
     "'North Kivu', 'clashes' and the three suspended sites appear in no cited source."),
    (14, "summary",
     "Three Fed officials dissented in favour of a rate hike",
     "Governors Michelle Bowman, Christopher Waller and Lisa Cook dissented in favour of a rate hike",
     "attribution-upgrade",
     "Cited sources say 'three Fed officials'; none of the three names appears in any cited source."),
    (16, "summary",
     "according to government data published Friday.",
     "with 731,000 babies born, a 1.8 percent increase the ministry attributed to a post-pandemic rebound in marriages, according to government data published Friday.",
     "OutE-padding",
     "Sole source is a one-line headline with no fulltext; every figure and the causal attribution are invented."),
    (15, "summary",
     "finished second to incumbent President Hakainde Hichilema.",
     "finished second to incumbent President Hakainde Hichilema by fewer than 40,000 votes.",
     "OutE-absence",
     "No cited source states any vote margin."),
    (5, "why_it_matters",
     "that before the war carried roughly one-fifth of world oil supply.",
     "that before the war carried roughly one-fifth of world oil supply -- a closure the Biden administration has so far failed to reverse.",
     "stale-world-state",
     "Cited sources describe the Trump administration; 'Biden' appears in no cited source."),
]

# Stories left entirely untouched -> false-positive denominator. idx 10 is
# EXCLUDED from both sets: production COHERENCE flagged it on the real run, so it
# is not a trustworthy negative.
CLEAN_IDX = [1, 3, 4, 7, 8, 9, 11, 12]
FIELDS = ("headline", "summary", "why_it_matters")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for f in list(OUT.glob("*")):
        f.unlink()
    for name in ("article_fulltext.json",):
        shutil.copy(SRC / name, OUT / name)
    for p in SRC.glob("articles_*.csv"):
        shutil.copy(p, OUT / p.name)

    ds = json.loads((SRC / "draft_selections.json").read_text())
    stories = ds["must_know"] + ds["should_know"]

    hard = []
    for idx, field, old, new, typ, why in PLANTS:
        cur = stories[idx][field]
        if old not in cur:
            raise SystemExit(f"plant idx={idx} field={field}: anchor not found:\n  {old!r}\n  in {cur!r}")
        stories[idx][field] = cur.replace(old, new, 1)
        hard.append({"idx": idx, "field": field, "type": typ, "claim": new, "why": why})

    (OUT / "draft_selections.json").write_text(json.dumps(ds, indent=2))

    labels = {
        "_doc": ("Held-out planted-fabrication ground truth built from archived run 278 "
                 "(2026-08-28 healthy run). 8 fabrications planted into 8 distinct stories, "
                 "one field each; each verified absent from or contradicted by that story's OWN "
                 "cited sources. clean_fields = the 3 fields of 8 entirely UNTOUCHED stories. "
                 "CAVEAT: 'untouched' means production COHERENCE passed the story and nothing was "
                 "planted -- it is not a hand-audited clean label, so the false-positive rate on "
                 "this set is an UPPER BOUND (a flag may be a genuine catch production missed). "
                 "idx 10 is excluded from both sets: production flagged it on the real run."),
        "hard_positives": hard,
        "borderline": [],
        "clean_fields": [{"idx": i, "field": f} for i in CLEAN_IDX for f in FIELDS],
        "idx_headlines": {str(i): s["headline"] for i, s in enumerate(stories)},
    }
    (OUT / "labels.json").write_text(json.dumps(labels, indent=2))
    print(f"built {OUT}: {len(hard)} hard positives, {len(labels['clean_fields'])} clean fields, "
          f"{len(stories)} stories")
    for h in hard:
        print(f"  idx {h['idx']:>2} {h['field']:<15} {h['type']}")


if __name__ == "__main__":
    main()
