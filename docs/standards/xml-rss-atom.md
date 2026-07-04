# XML / RSS / Atom standards reference

> As of 2026-07, verify before relying — re-check the linked specs/tool docs; parser defaults and CVEs shift.

Scope: this pipeline INGESTS RSS/Atom via feedparser (`newsroom/src/feeds.py`, `gnews.py`, `prepare.py`) and EMITS Atom 1.0 at `/feed.xml` (`circulation/src/feed.rs`). Feeds are untrusted third-party input. Treat every byte as hostile.

## 1. XML security (top priority)

- Python stdlib parsers (`xml.etree.ElementTree`, `minidom`, `sax`, `pulldom`) are vulnerable to XXE, billion-laughs / exponential + quadratic entity expansion, and external-DTD / external-general-entity SSRF. Do NOT feed them untrusted XML.
- Use **`defusedxml`** for any untrusted XML: drop-in secure substitutes; `forbid_entities=True` (default) raises `EntitiesForbidden` on `<!ENTITY>` in the DTD; also blocks DTD-retrieval and external-entity resolution. `pip install defusedxml`, then `from defusedxml.ElementTree import parse`.
- feedparser (used here): modern versions disable entity expansion / external entities by design, but this is a library guarantee, not the stdlib default — pin the version, don't hand it to `ElementTree` afterward, and never re-parse a feed with a raw stdlib parser.
- If forced to use **lxml** on untrusted input: `XMLParser(resolve_entities=False, no_network=True, dtd_validation=False, load_dtd=False, huge_tree=False)`. `no_network=True` blocks external-entity/DTD fetches; `resolve_entities=False` stops expansion.
- Billion-laughs = tiny payload, GBs of RAM (nested `&lol;` entities). Entity-expansion defence must be on regardless of XXE.
- Feed-supplied HTML (titles, summaries, `content`) is untrusted markup. NEVER render or store it raw — sanitize (allowlist tags/attrs, strip `<script>`, `on*=`, `javascript:` URIs) before display or DB write. feedparser's `feed.sanitize_html` is on by default but do not rely on it as the only layer.

## 2. Generating XML / Atom correctly

- **NEVER hand-build XML by string concatenation, f-strings, or `format!`.** Escaping, CDATA, namespaces, and encoding are too easy to get subtly wrong (unescaped `&`, `<`, `"` in attributes; control chars; wrong entity refs).
- Use a real serializer: Python `lxml.etree` / `xml.etree.ElementTree` with `.text`/`.set` (auto-escapes) + `ET.tostring(encoding="utf-8", xml_declaration=True)`; Rust `quick-xml` (`Writer`/serde) or the `atom_syndication` crate (typed Atom builder).
- **REPO NOTE:** `circulation/src/feed.rs::render_atom_feed` currently hand-builds the Atom via `format!` + `escape_html`. This is the exact anti-pattern above and the main risk surface. Escaping correctness rides entirely on `escape_html` covering `& < > " '` in both text and attribute positions; a gap = malformed/injectable feed. Prefer migrating to `quick-xml` or `atom_syndication`. Until then: keep an emitted-feed validation test (§5) as the backstop.
- Let the serializer own the XML declaration + encoding; emit UTF-8 and declare it. Don't emit a BOM.
- Attribute values need the same escaping as text plus `"`/`'`; never interpolate URLs/user data into attributes unescaped.

## 3. Atom vs RSS specifics

- **Atom (RFC 4287)** required elements:
  - `feed`: exactly one `<id>` (stable, permanent IRI), one `<title>`, one `<updated>`; one-or-more `<author>` UNLESS every `<entry>` has its own author; SHOULD include `<link rel="self" type="application/atom+xml">`.
  - `entry`: exactly one `<id>`, one `<title>`, one `<updated>`; needs an author (own or inherited from feed) and content-or-summary.
- **Dates:** Atom uses **RFC 3339** (`2026-07-04T12:30:00Z`, all components present, offset or `Z` required). RSS 2.0 uses **RFC 822** (`Sat, 04 Jul 2026 12:30:00 GMT`). Do not mix them — RFC 3339 in an RSS `pubDate` or RFC 822 in Atom `<updated>` fails validators.
- **Stable IDs:** `<id>` / RSS `<guid>` must be globally unique and never change for a given item (permalink or `tag:` URI). Unstable ids = duplicate/re-notified items downstream. Our per-date entry id (`{base}/{date}`) is stable — keep it.
- **`xml:base` / relative URLs:** Atom resolves relative IRIs against `xml:base` (or the document URI). On ingest, resolve relative links to absolute before use. On emit, prefer absolute URLs to avoid reader ambiguity.
- **Content types:** Atom `<title>`/`<summary>`/`<content>` take `type="text"` (plain, escape literally), `type="html"` (escaped HTML string), or `type="xhtml"` (a well-formed inline `<div>`). Pick one and be consistent; `text` is safest for machine-generated fields.

## 4. Ingest defensively (feedparser is lenient by design)

- feedparser tolerates malformed feeds and returns partial data — expect mess, don't treat it as exceptional.
- **`bozo`:** `feed.bozo == 1` means not-well-formed; `feed.bozo_exception` holds why. bozo does NOT mean unusable — often entries still parsed. Current `feeds.py` rule (`if feed.bozo and not feed.entries: error`) is the right shape: only fail when there's genuinely nothing. Consider distinguishing `CharacterEncodingOverride` (benign) from `SAXParseException` (real).
- **Dates:** may be missing, ambiguous, or unparseable. feedparser gives `*_parsed` (a `time.struct_time` in UTC) when it can; it's `None` otherwise. Never assume presence; fall back (e.g. fetch time) and never crash on a bad date.
- **Encoding:** declared vs actual encoding conflicts set bozo (`CharacterEncodingOverride`); `CharacterEncodingUnknown` when undetectable. feedparser usually recovers; log, don't abort.
- **Namespaces / format variance:** RSS 1.0/2.0, Atom 0.3/1.0, Dublin Core, media/content extensions all differ. Read via feedparser's normalized keys (`entry.link`, `entry.title`, `entry.published_parsed`), not raw tag names. Guard every field with `.get(...)` / presence checks; namespaced extensions may be absent.

## 5. Validation (CI)

- Validate the emitted `/feed.xml` in CI — don't ship on "looks right".
- **Round-trip:** parse the emitted feed with feedparser; assert `bozo == 0` (or only benign) and required elements present: feed `id`/`title`/`updated`/`author`/`link rel="self"`; each entry `id`/`title`/`updated`. This catches escaping/structure regressions from the hand-built emitter.
- **W3C Feed Validator rules** (RFC 4287 conformance): valid RFC 3339 dates, unique/absolute ids, correct `rel`/`type` on links. Encode the key rules as assertions rather than hitting the live web service in CI.
- Assert well-formedness by re-parsing with a strict XML parser (fails on unescaped `&`, bad namespaces, stray control chars).

## 6. Common pitfalls / anti-patterns

- Unescaped `&` (and `<`, `>`, `"`) — the classic hand-built-XML break; a serializer prevents it.
- Naive date parsing / assuming a field exists — use feedparser's `*_parsed`, fall back gracefully.
- Trusting feed HTML — sanitize before render/store; assume it carries XSS.
- Mixing RSS and Atom conventions — RFC 822 vs RFC 3339 dates, `guid` vs `id`, `pubDate` vs `updated`/`published`.
- Handing untrusted XML to a stdlib parser (XXE/billion-laughs) — use `defusedxml` or a hardened parser config.
- Unstable `<id>`/`<guid>` — breaks dedup and re-notifies readers.
- Interpolating URLs into XML attributes without escaping.

## References

- [RFC 4287 — Atom Syndication Format](https://www.rfc-editor.org/rfc/rfc4287)
- [W3C Feed Validator — Introduction to Atom](https://validator.w3.org/feed/docs/atom.html) · [InvalidRFC3339Date](https://validator.w3.org/feed/docs/error/InvalidRFC3339Date.html)
- [defusedxml (PyPI)](https://pypi.org/project/defusedxml/) · [github.com/tiran/defusedxml](https://github.com/tiran/defusedxml)
- [feedparser — Bozo Detection](https://feedparser.readthedocs.io/en/stable/bozo.html) · [Character Encoding Detection](https://feedparser.readthedocs.io/en/latest/character-encoding.html)
