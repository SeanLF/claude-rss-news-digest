---
title: An absence assertion cannot pin a value whose default is the thing you are guarding against
date: 2026-09-03
category: test-failures
module: ops, testing
problem_type: test_failure
severity: medium
applies_when:
  - Writing a test that guards a security or safety property
  - The assertion is of the form "the dangerous string is not present"
  - The property is enforced by an explicit flag, suffix, or option that has a default
tags: [testing, negative-control, security, read-only, docker, discrimination]
---

## The lesson

`assert "bad" not in x` only guards the property when "bad" is the *only* way to lose it.
If the hazardous behaviour is also the **default** — what you get by writing nothing at all —
then deleting the safety token passes the test. Assert the safe value is present, not that the
unsafe one is absent.

Then break the property on purpose and watch the test fail. A test that has never failed has
not been shown to discriminate.

## Where it bit

`bin/ops` reads the production database from a container with the data volume mounted. The
read-only guarantee is the whole reason it is allowed to exist, and one of its two layers is
the `:ro` suffix on the mount:

```
-v news-digest-data:/d:ro
```

The test guarding it asserted:

```python
assert ":rw" not in cmd
```

Docker's default mount mode, with no suffix, is **read-write**. So `-v news-digest-data:/d`
— the exact one-token regression the test exists to catch — contains no `:rw` and passed.
Adversarial review found it by deleting the suffix and re-running the assertions.

The same review found the sibling case: a test asserting a hostile artifact name did not
appear in the generated payload's *text*, while the real value travelled by environment
variable into that payload at runtime. A version of the script that concatenated the
environment value straight into SQL passed every assertion.

## What to do instead

- **Positive assertion:** `assert f"-v {VOLUME}:/d:ro " in cmd`. Include the delimiter, so a
  corrupted variant does not match on a prefix.
- **Execute the real path.** If the value reaches the code through an environment variable at
  runtime, the test has to set that variable and run it. Inspecting the text that surrounds it
  tests a different thing than the one that can break.
- **Negative-control every safety test.** Delete the suffix, replace the binding with string
  concatenation, and confirm the relevant test — and only that test — fails.

## The trap inside the trap

The first attempt at those negative controls used `sed -i ''` with an unescaped pattern and a
Python heredoc with a quoting error. Neither edit applied. Both runs reported **21 passed**,
which read exactly like "the tests are fine" and actually meant "nothing was broken, so
nothing was tested". The controls only mean something once you have confirmed the break landed:

```python
assert old in t, "anchor not found"
...
print("BREAK APPLIED:", marker in Path(path).read_text())
```

A broken instrument exits 0. See `a-canary-nothing-runs-is-not-a-canary.md` — the same shape,
one level up.

## See also

- `docs/2026-09-03-ops-access-review.md` — the review, the fixes, and the controls.
- `best-practices/test-the-detectors-not-the-happy-path.md`
- `an-audit-record-of-the-verdict-cannot-audit-the-verdict.md`
