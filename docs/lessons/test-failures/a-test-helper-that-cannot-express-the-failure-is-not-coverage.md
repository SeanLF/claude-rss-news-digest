---
title: A test helper that cannot construct the failing input makes a whole suite structurally blind, and every test still passes
date: 2026-07-28
category: test-failures
module: gnews, googlenewsdecoder
problem_type: test_failure
severity: high
applies_when:
  - A test suite builds its own fixtures with a hand-written encoder
  - Every test in a file uses inputs of a similar size or shape
  - A green suite is being used as evidence that a rewrite preserved behaviour
tags: [testing, fixtures, boundary-values, varint, protobuf, false-confidence]
---

A decoder test suite had 114 passing tests over a function that was silently corrupting
every input above a threshold. The tests were not weak. They could not reach the bug.

The helper that built test tokens was:

```python
body = bytes([0x08, 0x13, 0x22]) + bytes([len(payload)]) + payload.encode() + ...
```

`bytes([len(payload)])` writes one raw byte. The real format is a protobuf varint: one byte
below 128, two bytes above. So the helper could not construct a valid token for any payload of
128 bytes or more, and raised outright at 256. Every fixture in the suite was short — the
longest was 45 characters — and the production code read the length back the same wrong way.

**Encoder and decoder shared the same misunderstanding, so they agreed perfectly.** Every test
passed. The bug was that URLs of 128+ bytes came back one character short, and past 255 the
length was wrong outright. Because a truncated URL still starts with `http`, it was returned as
a *successful* decode rather than an error — the caller just fetches a broken link.

## What to do

**Parameterise across the boundary, not around it.** The fix was one test:

```python
@pytest.mark.parametrize("length", [1, 100, 126, 127, 128, 129, 200, 255, 256, 300, 1000])
def test_a_payload_of_any_length_survives_the_round_trip(self, length):
```

127/128 and 255/256 are where a length encoding changes width. Any field with a length prefix,
a continuation bit, or a size class has boundaries like these, and they are cheap to enumerate.

**Treat a hand-rolled fixture builder as untested production code**, because that is what it
is. If it encodes a format, the format has edge cases, and the builder needs the same scrutiny
as the parser. Better still, build fixtures with a real encoder or a captured real sample.

**Ask what input would fail, then check the helper can even express it.** If it cannot, the
suite's green is measuring the helper's range, not the code's correctness. That question is
fast and it is the one that would have caught this on day one.

## Why it mattered here

This was found while collapsing five duplicate implementations into one. The arithmetic had
been copied into all five, so it looked correct by consensus — five agreeing copies of one
misreading. Consensus among copies is not verification; see
[[hand-copied-constants-drift-silently]].

Related: [[verify-the-validation-run-contains-the-code-under-test]].
