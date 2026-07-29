---
title: A protocol constant copied into N places will be wrong in N places, and agreement between the copies reads as confirmation
date: 2026-07-28
category: best-practices
module: gnews, googlenewsdecoder, probes
problem_type: best_practice
severity: high
applies_when:
  - The same request envelope, magic number or wire format appears in more than one file
  - A measurement script hand-rolls the request instead of calling the library it measures
  - A version number keeps incrementing on the same component
tags: [duplication, protocol, single-source-of-truth, measurement, instrumentation]
---

`googlenewsdecoder` shipped four decoder generations. Each carried its own copy of the
batchexecute request envelope and its own copy of the length-parsing arithmetic. The version
count was not a record of features; it was a record of a Google change needing four edits and
getting a new file instead.

Two failures follow from copies, and the second is worse.

**The copies are wrong together.** All four read a protobuf varint length as a single raw byte.
Reviewing any one of them against the others confirmed it. Four agreeing implementations feel
like evidence, but they share an ancestor, so they share its mistakes. Consensus among copies
is one opinion wearing four hats.

**A copy in the measuring instrument corrupts the measurement.** A probe measuring Google's
rate limits hand-rolled the RPC body rather than calling `protocol.batch_decode_request`. This
project had already paid for that once: a malformed envelope made *every* request fail, and
the resulting failures were attributed to token expiry — a conclusion that survived into
documentation as fact and had to be retracted after direct testing. See
[[verify-the-validation-run-contains-the-code-under-test]].

An instrument that drifts from the thing it measures does not go quiet. It keeps reporting
plausible numbers about a request nobody makes.

## What to do

**One definition, imported everywhere** — including into scripts, probes and benchmarks. The
usual objection is that a test or probe should be independent of the library so it can catch
the library being wrong. That applies to *assertions*, not to the wire format: a probe
measuring an endpoint's throttling is measuring the endpoint, and using the real request is
what makes the number mean anything.

**Read a rising version number as a duplication smell.** `v1, v2, v3, v4` of one component
usually means each break got a new copy rather than a fix, because fixing meant editing every
copy. The cost compounds: five implementations, five contracts, and no single place to change.

**When collapsing duplicates, diff their behaviour rather than reading them.** They will look
equivalent. Drive both with the same scripted inputs and compare outputs — that is how the
divergences surface, including the ones every copy shares.
