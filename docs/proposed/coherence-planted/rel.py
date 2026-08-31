"""scratch/sourcing/relevance.py's scorer, lifted verbatim (tokeniser, stoplist, Jaccard, floor).

Story text = headline + summary, article text = its RSS title. Floor 0.06, hand-validated on the
run-282 desertification case and negative-controlled at 19.4x on runs 276/280.

KNOWN MISS, carried through every number below: it scored A606 -- the genuinely-related WSJ drought
piece -- at 0.057, just under the floor. So "junk" is an UPPER bound, which is why the floor is
swept at 0.04/0.06/0.08 rather than asserted.
"""
import re

_W = re.compile(r"[a-z0-9]{3,}")
_STOP = set("""the and for with from that this into over after says say said new news week year
years first two three most more than has have had was were are its his her their they them
about against amid as at be been by can could do does during each has how in is it may might
of on or out shall should since so some such then there these those to under up upon was we
what when where which while who will would you your also other another back down off out""".split())

FLOOR = 0.06
FLOORS = (0.04, 0.06, 0.08)


def toks(t: str) -> set[str]:
    return {w for w in _W.findall((t or "").lower()) if w not in _STOP}


def jac(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def story_tokens(story: dict) -> set[str]:
    return toks(f"{story.get('headline','')} {story.get('summary','')}")


def score(story_toks: set[str], title: str) -> float:
    return jac(story_toks, toks(title))
