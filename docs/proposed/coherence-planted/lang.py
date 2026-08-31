"""Does the gate strip NON-ENGLISH corroboration?

Hand adjudication flagged this: 4 of the gate's 7 false positives were Spanish/German articles
reporting the SAME event as the story, cut because MiniLM-L6-v2 is an English-only encoder and
cross-lingual cosine collapses. The lexical relevance metric cannot arbitrate -- it is also
English token overlap, so it scores those articles as junk for the same wrong reason.

So measure it two ways that do not share that blind spot:
  1. cut RATE for non-English vs English citations (a rate ratio, so it is not confounded by how
     many non-English citations there are);
  2. re-run the identical gate on paraphrase-multilingual-MiniLM-L12-v2. If the non-English cut
     rate collapses under a multilingual encoder while the English rate holds, the excess is the
     encoder's language blindness and not a real relevance judgment.

Language is assigned per SOURCE (der_spiegel de, clarin_mundo es, le_monde fr) -- checked against
the corpus, these three are the only feeds publishing in their own language; france24, DW, SCMP and
Nikkei all carry English feeds here.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rel  # noqa: E402

NON_EN = {"der_spiegel": "de", "clarin_mundo": "es", "le_monde": "fr"}
RULE = "plurality"


def table(rows, label):
    agg = defaultdict(lambda: [0, 0])  # [cited, cut]
    for x in rows:
        cut = set(x[f"cut_{RULE}"])
        for a in x["cited"]:
            k = NON_EN.get(x["sources"].get(a, ""), "en")
            agg[k][0] += 1
            agg[k][1] += a in cut
    en_n, en_c = agg["en"]
    ne_n = sum(v[0] for k, v in agg.items() if k != "en")
    ne_c = sum(v[1] for k, v in agg.items() if k != "en")
    print(f"\n{label}")
    print(f"{'language':<12}{'citations':>11}{'cut':>7}{'cut rate':>11}")
    print(f"{'English':<12}{en_n:>11}{en_c:>7}{100*en_c/en_n:>10.1f}%")
    for k in sorted(NON_EN.values()):
        n, c = agg[k]
        if n:
            print(f"{k:<12}{n:>11}{c:>7}{100*c/n:>10.1f}%")
    print(f"{'NON-ENGLISH':<12}{ne_n:>11}{ne_c:>7}{100*ne_c/ne_n:>10.1f}%"
          f"    rate ratio vs English: {(ne_c/ne_n)/(en_c/en_n):.2f}x")
    return (en_c / en_n, ne_c / ne_n)


mini = json.loads((HERE / "rows.json").read_text())
table(mini, "MiniLM-L6-v2 (English-only) -- the gate as evaluated at cluster level")

alt = HERE / "rows_mmnilm.json"
if alt.exists():
    mm = json.loads(alt.read_text())
    table(mm, "paraphrase-multilingual-MiniLM-L12-v2 -- same gate, multilingual encoder")
    for label, rows in (("minilm", mini), ("mmnilm", mm)):
        for floor in rel.FLOORS:
            base_j = sum(sum(1 for a in x["cited"] if x["scores"][a] < floor) for x in rows)
            base_n = sum(len(x["cited"]) for x in rows)
            kept = [(a, x) for x in rows for a in x[f"kept_{RULE}"]]
            kj = sum(1 for a, x in kept if x["scores"][a] < floor)
            print(f"  {label} floor {floor:.2f}: junk-citation rate "
                  f"{100*base_j/base_n:.1f}% -> {100*kj/len(kept):.1f}%   "
                  f"cites/story {base_n/len(rows):.2f} -> {len(kept)/len(rows):.2f}")
else:
    print("\n(run e2e with model='mmnilm' first to produce rows_mmnilm.json)")

print("\nNON-ENGLISH CITATIONS THE GATE CUT (all of them, for reading):")
n = 0
for x in mini:
    for a in x[f"cut_{RULE}"]:
        if x["sources"].get(a, "") in NON_EN:
            n += 1
            print(f"  [{NON_EN[x['sources'][a]]}] {x['titles'][a][:88]}")
            print(f"       under: {x['headline'][:88]}")
print(f"  ({n} of them)")
