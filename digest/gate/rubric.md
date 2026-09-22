Return a JSON array of `{story, criterion, pass, reason}` objects, one per story per criterion, and
nothing else. `story` is the 0-based index in the digest's must_know order; `criterion` is the number
below; `reason` is one line. You are given the rendered digest and the day's article CSVs; you never see
URLs. Absence of a specific in the cited sources is a fail; paraphrase and compression are not.

1. **Supported.** Every specific in headline, summary and why_it_matters (number, date, name, place, quote,
   quantifier) appears in that story's cited sources. Absence is a fail; paraphrase and compression are not.
2. **Bound correctly.** Each specific is attached to the entity and predicate the sources give it.
3. **Not stale.** Office-holders, administrations and world state match the cited articles and the run date.
4. **Earns its slot.** why_it_matters adds a mechanism, contradiction or consequence the summary does not already
   say; filler fails.
5. **One event per story.** A headline or summary that bolts a second event on fails.
6. **Selection.** The must_know set is the day's most consequential stories given the inputs; a story a reader
   of the inputs would expect and does not find is named.
7. **Reads clean.** No internal ids, no template tokens, no truncation; preheader within its cap.
