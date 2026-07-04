# JSON standards reference (mid-2026)

> As of 2026-07: this reflects current spec/tooling reality, but verify draft/version status before relying on anything version-sensitive (JSON Schema is still pre-stable and moving).

## Spec + interop essentials

- **RFC 8259** (2017) is the authoritative IETF JSON standard (STD 90); aligned with **ECMA-404**. Still current in 2026 — no successor RFC.
- **RFC 7493 (I-JSON)** is the stricter interop profile: UTF-8 only, no duplicate keys, numbers within IEEE-754 double range. Treat subagent/API JSON as I-JSON.
- **Encoding is UTF-8.** Do not emit BOMs; do not assume other encodings on read.
- **No comments, no trailing commas** in real JSON. If a `.json` file has either, it is JSON5/JSONC, not JSON — a strict parser will reject it.
- **Object key order is not semantically significant.** Don't rely on it for correctness. (In practice the mainstream parsers *do* preserve insertion order — CPython `dict`, V8 for string keys — but the spec doesn't require it and other consumers may not, so treat preservation as an implementation detail, not a guarantee.)
- **Duplicate keys** are permitted by RFC 8259 but I-JSON forbids them; behaviour is parser-defined (usually last-wins). Never emit them.
- Top-level value may be any JSON value (RFC 8259), but prefer an object at the root for extensibility.

## JSON Schema

- **Current stable dialect: draft 2020-12.** This is what to target today.
- **Trajectory — "JSON Schema 2026":** the next release drops the perpetual-draft model for a **stable line** with backward/forward-compat guarantees. The one consequential break to plan for: it **disallows unknown keywords by default** (any keyword not defined by a vocabulary in the meta-schema is forbidden), reversing 2020-12's ignore-unknowns behaviour. That directly affects how `SELECTIONS_SCHEMA` is written here — annotations/extra keys that 2020-12 silently ignores will become hard errors, so keep the schema clean of non-vocabulary keywords now to avoid a migration break later.
- Always set `"$schema": "https://json-schema.org/draft/2020-12/schema"` so validators pick the right dialect.
- 2020-12 array-keyword shift: `prefixItems` + `items` (the old `items`+`additionalItems` are gone); dynamic refs are `$dynamicRef`/`$dynamicAnchor`.
- Core validation keywords: `type`, `required`, `properties`, `enum`, `const`, `minimum`/`maximum`, `minItems`/`maxItems`, `pattern`.
- **`"additionalProperties": false`** to reject unknown keys — essential at a trust boundary (subagent output). Note it does not "see through" `allOf`/`$ref` composition; unknown-key rejection there needs `unevaluatedProperties: false`.
- **`$ref`** for reuse (local `#/$defs/...` or external). `$defs` is the 2020-12 home for reusable subschemas.
- **`format`** (e.g. `date-time`, `email`, `uri`) is an *annotation, not an assertion* by default — most validators do not enforce it unless format-assertion mode is enabled. Do not depend on `format` to validate; pair with `pattern` if you need enforcement.
- Use schema validation as a **hard gate** at ingestion (this repo already does: `SELECTIONS_SCHEMA` in `newsroom/src/schema.py`, applied in `merge.py`). Fail closed on invalid input.
- **Validators:** in Python, the `jsonschema` lib (in-code checks) and **`check-jsonschema`** (CLI + pre-commit hook — fits this repo's CI gate for validating `*.json` against a schema). Cross-language: **`ajv`** (JS, the de-facto fast one) and **Sourcemeta Blaze** (C++, compiles schemas for high-throughput validation).

## Number / precision pitfalls

- JSON numbers are conceptually arbitrary-precision text, but almost every parser loads them as **IEEE-754 doubles**. Safe integer range is ±2^53−1 (`Number.MAX_SAFE_INTEGER`).
- Integers above 2^53 (large IDs, snowflake IDs, token counts if ever huge) **silently lose precision** — carry them as **strings**.
- **Money: never store as float.** Use integer minor units (cents) or a decimal string. Floats give `0.1 + 0.2 != 0.3`.
- **`NaN`, `Infinity`, `-Infinity` are NOT valid JSON.** Python's `json.dumps` emits them by default (`allow_nan=True`) producing non-conformant output — set `allow_nan=False` for anything crossing a boundary, or represent as `null`/string.
- Do not use numbers as object keys (JSON coerces all keys to strings anyway) — be explicit and use string keys.

## JSONL / NDJSON (streams + logs)

- **One JSON value per line, `\n`-separated, no wrapping array.** Each line is independently parseable.
- Prefer over a single JSON array when: the file is **append-only** (logs), **streamed**, **very large**, or processed line-by-line without loading the whole thing. This is exactly the Claude session-log case (`usage.py` parses per-line).
- Append is trivial and crash-safe (a torn final line is discardable); a JSON array requires rewriting the closing bracket.
- Read defensively: **skip/log blank lines and malformed lines** rather than aborting the whole file — session logs can have partial trailing writes.
- No comments, no multi-line objects — the whole point is line = record.
- **RFC 7464 (JSON Text Sequences, `application/json-seq`)** is JSONL's IETF-standardised cousin — records delimited by a leading `RS` (0x1E) control char rather than bare newlines, so a torn record is unambiguously skippable. JSONL is the ubiquitous de-facto form; RFC 7464 is the spec to cite if a consumer needs a formal one.
- **Stream processors** for line-oriented JSON: **`jaq`** (Rust, jq-compatible, fast) and **`gojq`** (Go, jq-compatible, better error messages) as `jq` replacements; **`qj`** (simdjson-backed) is fast on large NDJSON — relevant to `usage.py`'s per-line JSONL parsing if that ever becomes a throughput concern.

## Best practices

- **Validate at trust boundaries**, not everywhere. Subagent → Python handoff is a boundary; schema-check there.
- **Explicit `null` vs key omission carry different meaning** — decide and document per field. Omission = "not provided"; `null` = "known to be empty". Don't use them interchangeably.
- **Stable `snake_case` keys.** Consistency > cleverness; renaming keys is a breaking change for every consumer.
- **Time as ISO-8601 / RFC 3339 strings** (`2026-07-04T12:25:00Z`), UTC with explicit offset. Never epoch-as-number if humans read it; never dates as bare numbers.
- **Stream large files** (`ijson` in Python, or line-by-line for JSONL) instead of loading multi-MB blobs into memory.
- Emit deterministic output where diffs matter (`sort_keys=True`, fixed float formatting) so intermediate `*.json` files diff cleanly.
- Keep schemas versioned alongside code; a schema change is an API change.

## Anti-patterns + when to reach for alternatives

- **Don't hand-edit generated JSON** as if it allowed comments/trailing commas — it doesn't.
- **Don't parse JSON with regex.** Use a real parser.
- **Don't trust `format` for validation** (see above) or key order for logic.
- **Don't put secrets or huge blobs inline** — reference them.
- Config that humans edit and want comments in → **JSON5 / JSONC** (comments, trailing commas) or **TOML** (typed, comment-friendly, good for flat config) or **YAML** (nested config; beware the Norway problem and indentation footguns). Keep the wire/handoff format plain JSON.
- Size/throughput-critical binary → **MessagePack** (drop-in JSON-like) or **Protobuf/Avro** (schema'd, compact). Not warranted for this repo's file-based handoff — plain JSON + JSON Schema is the right call here.

## Sources

- [RFC 8259 (JSON)](https://www.rfc-editor.org/rfc/rfc8259)
- [RFC 7493 (I-JSON)](https://datatracker.ietf.org/doc/html/rfc7493)
- [JSON Schema specification](https://json-schema.org/specification)
- [JSON Schema 2020-12 release notes](https://json-schema.org/draft/2020-12/release-notes)
- [IETF JSON Schema draft (datatracker)](https://datatracker.ietf.org/doc/draft-ietf-jsonschema-json-schema/)
