# YAML standards reference

> As of 2026-07, verify before relying: loaders diverge on spec version, boolean/null coercion, and merge-key support. Test edge values against the *actual* loader you ship, don't assume from the spec.

Scope: this repo uses YAML for `docker-compose.yml`, `lefthook.yml`, `.claude/agents/*.md` frontmatter, and Python may parse config/YAML.

## The footguns (implicit typing / "Norway problem")

- **`NO` → `false`.** Norway's country code, and `no/yes/on/off/y/n/true/false` (any case) coerce to bool under YAML 1.1 rules — which most loaders still use. `country: NO` silently becomes `country: false`.
- **Version strings bite:** `version: 1.20` parses as float `1.2` (trailing zero lost); `version: 1.2.3` is a string (safe), but `1.0` is a float. Quote all version numbers: `"1.20"`.
- **Leading-zero octals:** `0755`, `08` — YAML 1.1 reads `0o…`-style as octal (and `08`/`09` may error). Zip codes, PINs, ordinal IDs lose leading zeros or misparse.
- **Sexagesimal:** YAML 1.1 reads `12:30:00` (unquoted) as a base-60 number, not a time string. MAC-address-like and time-like values need quotes.
- **Git SHAs / hashes:** an all-digit SHA (`0123456789`) parses as int and drops leading zeros; a SHA starting with `e`+digits (`e0`) can look like scientific notation. Quote SHAs.
- **Nulls:** bare `null`, `Null`, `~`, and *empty value* all become `None`/nil. `key:` with nothing after is null, not `""`.
- **Rule of thumb:** quote every scalar whose meaning is "text that happens to look like data" — country/currency codes, versions, SHAs, ports-as-strings, dates, times, phone/zip.

## YAML 1.1 vs 1.2 divergence (pin your expectations)

- YAML 1.2 (2009) narrowed bool to exactly `true`/`false` and aligned with JSON. But **PyYAML/LibYAML and most Go loaders still default to 1.1 semantics in 2026** — Docker Compose included. The Norway problem is alive because of the *loader*, not the spec.
- Don't assume "we're on 1.2, so `no` is a string." Verify: PyYAML 6.x targets 1.1; `ruamel.yaml` defaults to 1.2.
- **Merge key `<<` is NOT in YAML 1.2 core** (it was a 1.1 extension, dropped from the 1.2 core schema). Support is loader-dependent: PyYAML and Docker Compose support it; some strict 1.2 parsers do not. Verify before relying on anchor+merge for DRY configs.

## Security (Python)

- **Only `yaml.safe_load` / `yaml.safe_load_all`.** Never `yaml.load(...)` with the default/`FullLoader`/`UnsafeLoader` on any input you don't fully control — it can construct arbitrary Python objects (RCE via `!!python/object`). Even "trusted" config drifts into untrusted; default to safe.
- `safe_load` yields only dict/list/str/int/float/bool/None — sufficient for config.
- Need to **preserve comments/formatting on round-trip** (read-modify-write a YAML file)? Use `ruamel.yaml` with `typ='rt'`; use `typ='safe'` for the safe-load equivalent. PyYAML discards comments and reorders keys.

## Anchors, aliases, merge (`&` / `*` / `<<`) — DRY compose

- `&name` defines an anchor, `*name` references it, `<<: *name` merges a mapping. Useful to share service env/logging blocks in `docker-compose.yml`.
- Use judiciously: deep anchor webs hurt readability and diff clarity. Prefer a shared anchor for one obvious repeated block, not a clever inheritance tree.
- Compose also has native `extends:` and `x-` extension fields — often clearer than raw anchors for reuse. `x-`-prefixed top-level keys are ignored by Compose and are the idiomatic anchor home.

## Tooling

- **`yamllint` in CI** — catches truthy-value ambiguity (the `truthy` rule flags unquoted `yes/no/on/off`), indentation, line length, trailing spaces, duplicate keys. Add a `.yamllint` config; wire into `make ci`. (Note: yamllint parses via PyYAML, so it sees 1.1 semantics.)
- **`.editorconfig`** — enforce 2-space indent, `indent_style = space`. **Tabs are invalid for indentation in YAML** (spec-level), a frequent silent breakage.
- Format on save; many editors auto-detect `.yml`/`.yaml`.

## Best practices & pitfalls

- **Explicit quoting** for anything ambiguous (see footguns). Single quotes = literal (only `''` escapes); double quotes = C-style escapes (`\n`, `\t`, `\u…`). Prefer single unless you need escapes.
- **Block style over flow style** for readability: prefer block maps/lists over `{a: 1, b: 2}` / `[1, 2]`. Flow is fine for short inline lists.
- **2-space indent, spaces only, no tabs.** Consistent indent per file.
- **No trailing whitespace/tabs.** Trailing tab after a value is a common lint failure.
- **One document per file** unless you deliberately use `---` separators (`safe_load_all` for multi-doc).
- **Comments** with `#` (space after `#`); YAML has no block comments — each line needs `#`.
- **Duplicate keys** are silently last-wins in many loaders — lint for them.
- **Multiline strings:** `|` (literal, keeps newlines), `>` (folded, joins to spaces); `|-`/`>-` strip the trailing newline. Know which you need for embedded scripts (lefthook commands).
- **Prefer JSON-safe values** where possible: `true`/`false`/`null` lowercase, quoted strings — reduces cross-loader surprise.
- `.yaml` is the recommended extension per spec, but this repo/Docker Compose convention is `.yml` — match the surrounding files.

## Sources

- [The Norway Problem — HitchDev/StrictYAML](https://hitchdev.com/strictyaml/why/implicit-typing-removed/)
- [YAML Implicit Typing Pitfalls — DevToys](https://devtoys.pro/blog/yaml-implicit-typing-pitfalls)
- [Norway problem persists despite 1.2](https://ascii.co.uk/news/article/news-20260118-2768a796/yaml-norway-problem-persists-despite-v12-spec-fix)
- [YAML in Python: PyYAML, safe_load, ruamel](https://kolavistudio.com/yaml-tools/python)
- [Tips from the hell of PyYAML — Reorx](https://reorx.com/blog/python-yaml-tips/)
- [YAML: The Missing Battery in Python — Real Python](https://realpython.com/python-yaml/)
- [ruamel.yaml basic use](https://yaml.dev/doc/ruamel.yaml/basicuse/)
