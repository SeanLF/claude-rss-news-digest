# Architecture Generalisation Plan

How to extend this pipeline to support arbitrary content verticals (ML papers, niche industry news, etc.) without rewriting it for each one.

---

## 1. Hardcoded Taxonomy Inventory

| File | What is hardcoded | Change required |
|------|-------------------|-----------------|
| `newsroom/src/render.py:19-28` | `REGION_CONFIG` dict mapping region keys to display names + emoji; `REGION_ORDER` list | Replace with vertical config: `sections` list with `key`, `display_name`, `emoji` |
| `newsroom/src/render.py:95-107` | `calculate_reading_time()` hardcodes tier names `must_know`, `should_know` and signals structure | Drive tier iteration from config |
| `newsroom/src/render.py:327-352` | `render_digest()` hardcodes `must_know`, `should_know`, `signals` keys; iterates `REGION_ORDER` for signals | Drive from tier + section config |
| `newsroom/src/render.py:369-398` | `extract_headlines()` hardcodes `must_know`, `should_know`, `signals` + `REGION_ORDER` iteration | Drive from config |
| `newsroom/src/render.py:407-412` | `format_story_counts()` hardcodes tier names with fixed emoji medals | Drive from config |
| `newsroom/src/digest.py:33-38` | `load_selections()` hardcodes tier names and minimum count warnings (must_know 3+, should_know 5+) | Drive minimums from config |
| `newsroom/src/digest.py:163-165` | `write_digest()` hardcodes tier names in log message | Minor, drive from config |
| `newsroom/src/mcp_server.py:87-98` | `SIGNALS_SCHEMA` hardcodes the five region keys as required properties | Generate schema dynamically from section list |
| `newsroom/src/mcp_server.py:100-125` | `SELECTIONS_SCHEMA` hardcodes `must_know`, `should_know`, `signals`, `preheader` as required | Generate schema dynamically from tier + section config |
| `newsroom/src/mcp_server.py:130-141` | `TOOLS[0]` description text refers to news-specific counts ("3+ stories", "5+ stories", regions) | Template the description from config |
| `newsroom/src/prepare.py:51-53` | Sources CSV written with columns `id, name, bias, factuality, perspective` -- `bias` and `factuality` are news-domain fields | Source schema should be vertical-specific; CSV columns should reflect available metadata |
| `.claude/agents/select.md:21-35` | Tier definitions with news-specific heuristics; region list hardcoded in schema output block; interest priority table (geopolitics, tech, celebrity filter); US domestic exception rule | Swap wholesale per vertical, or template tier/section definitions + inject vertical-specific editorial rules as a block |
| `.claude/agents/select.md:48-58` | Output JSON schema hardcodes region keys (`americas`, `europe`, `asia_pacific`, `middle_east_africa`, `tech`) | Template from section config |
| `.claude/agents/write.md:18-43` | Writing style ("The Economist meets AP wire"), `why_it_matters` framing, `reporting_varies` concept, preheader definition | Swap wholesale or inject as a style block |
| `.claude/agents/write.md:47-73` | Output schema hardcodes region keys | Template from section config |
| `.claude/commands/news-digest-select.md` | Dispatcher is largely structural (orchestration) but references `must_know`, `should_know`, `signals`, `preheader` in verification steps | Extract validation key names to config; mostly reusable as-is |
| `newsroom/sources.json` | `bias` and `factuality` fields are news-specific; `perspective` field is news-perspective-specific | The schema is open-ended enough that extra fields work; but bias/factuality rendering in the HTML (render.py:258, 284) would be wrong for non-news verticals |
| `newsroom/templates/digest-template.html` | Uses `{{MUST_KNOW}}`, `{{SHOULD_KNOW}}`, `{{SIGNALS}}` placeholders | Template per vertical, or generate placeholders dynamically from tier config |
| `newsroom/src/render.py:258` | `render_article()` renders `bias` field in sources line and `reporting_varies` list | These are news-specific concepts; ML papers vertical has no bias, has authors instead |
| `circulation/src/templates.rs` (rendered HTML) | `/sources` page renders `bias` colour spectrum and factuality badges (news concepts) | Source page needs to be vertical-aware or configurable |

---

## 2. Proposed Vertical Config Schema

A single `vertical.json` or `vertical.yaml` file per deployment that the pipeline reads at startup. Everything hardcoded above becomes a field here.

### Current news digest

```json
{
  "id": "news-digest",
  "display_name": "News Digest",
  "content_type": "news",

  "tiers": [
    {
      "key": "must_know",
      "display_name": "Must Know",
      "min_items": 3,
      "include_reporting_varies": true,
      "include_why_it_matters": true
    },
    {
      "key": "should_know",
      "display_name": "Should Know",
      "min_items": 5,
      "include_reporting_varies": false,
      "include_why_it_matters": true
    }
  ],

  "sections": [
    {"key": "americas",           "display_name": "Americas",             "emoji": "🌎"},
    {"key": "europe",             "display_name": "Europe",               "emoji": "🌍"},
    {"key": "asia_pacific",       "display_name": "Asia-Pacific",         "emoji": "🌏"},
    {"key": "middle_east_africa", "display_name": "Middle East & Africa", "emoji": "🌍"},
    {"key": "tech",               "display_name": "Tech",                 "emoji": "🤖"}
  ],

  "source_metadata_fields": ["bias", "factuality", "perspective"],
  "show_source_bias": true,
  "show_reporting_varies": true,

  "style_block": "The Economist meets AP wire. Short sentences, short words. Lead with most important fact. Be specific. Hedge unverified claims. No journalese.",

  "editorial_rules": "Prioritise: geopolitics, tech/AI, privacy. Filter: celebrity, sports, lifestyle. US domestic only if it directly affects other countries. Ensure breadth across regions when a dominant story consumes the cycle.",

  "tier_definitions": {
    "must_know": "Stories you'd be embarrassed not to know. Major geopolitical shifts, significant deaths, major policy changes.",
    "should_know": "Important but not urgent. Developing situations, notable policy moves, significant tech announcements.",
    "signals": "One-liners worth tracking. Everything noteworthy that didn't make the tiers above. Grouped by region."
  },

  "preheader_instructions": "One sentence capturing 2-3 biggest stories. Max 150 characters. No links."
}
```

### ML papers digest

```json
{
  "id": "ml-papers",
  "display_name": "ML Papers Digest",
  "content_type": "academic",

  "tiers": [
    {
      "key": "breakthrough",
      "display_name": "Breakthroughs",
      "min_items": 2,
      "include_reporting_varies": false,
      "include_why_it_matters": true
    },
    {
      "key": "notable",
      "display_name": "Notable Work",
      "min_items": 4,
      "include_reporting_varies": false,
      "include_why_it_matters": true
    }
  ],

  "sections": [
    {"key": "llms",          "display_name": "Large Language Models", "emoji": "🧠"},
    {"key": "vision",        "display_name": "Vision & Multimodal",   "emoji": "👁️"},
    {"key": "reasoning",     "display_name": "Reasoning & Agents",    "emoji": "🔗"},
    {"key": "efficiency",    "display_name": "Efficiency & Hardware",  "emoji": "⚡"},
    {"key": "applications",  "display_name": "Applications",          "emoji": "🔬"}
  ],

  "source_metadata_fields": ["institution", "venue", "open_access"],
  "show_source_bias": false,
  "show_reporting_varies": false,

  "style_block": "Technical but accessible. Lead with the key result. State the method in one clause. Quantify gains where data exists. Avoid hype ('revolutionary', 'game-changing'). Link claims to the paper's own numbers.",

  "editorial_rules": "Prioritise: novel architectures, state-of-the-art benchmark results, open-source releases with strong reproducibility. Filter: incremental ablations without new findings, preprints repeating prior work without citation.",

  "tier_definitions": {
    "breakthrough": "Papers that introduce a genuinely new idea or set a new benchmark that the field will cite.",
    "notable": "Solid contributions: useful techniques, important empirical findings, well-executed applications.",
    "signals": "One-line mentions: interesting preprints, dataset releases, tool announcements. Grouped by subfield."
  },

  "preheader_instructions": "One sentence summarising the 2-3 most significant papers. Max 150 characters."
}
```

Note: source entries in an ML vertical would have `institution` and `venue` instead of `bias` and `factuality`. The source CSV columns written by `prepare.py` and the article index fields would mirror whatever `source_metadata_fields` lists.

---

## 3. Prompt Templating Strategy

### Which agents to template vs. swap wholesale

| Agent | Template or swap | Rationale |
|-------|-----------------|-----------|
| `cluster.md` | **Template (light)** | Logic is content-agnostic: group articles covering the same story. The only vertical-specific part is the clustering heuristic hint, which can be an injectable block. |
| `recap.md` | **Template (trivial)** | Completely content-agnostic. The only change might be replacing "news" with "papers" in the prompt. One-line variable. |
| `select.md` | **Template (heavy)** | The tier definitions, region list in the output schema, interest priorities, and US domestic exception are all vertical-specific. These should be injected as blocks from config. |
| `write.md` | **Template (heavy)** | Writing style block, `why_it_matters` framing, `reporting_varies` concept (news-only), preheader instructions, and output schema regions are all vertical-specific. |
| `coherence.md` | **Template (trivial)** | Completely content-agnostic fact-checking. No domain knowledge baked in. |
| `news-digest-select.md` (dispatcher) | **Template (light)** | Orchestration structure is reusable. Only the verification keys (`must_know`, `should_know`, `signals`, `preheader`) need to be templated. |

### Templating mechanism

Jinja2 is the right choice -- it is already available in the Python environment (premailer pulls it in) and is readable at a glance. The dispatcher could render each agent prompt at runtime:

```python
# claude.py or a new prompts.py
from jinja2 import Environment, FileSystemLoader

def render_agent_prompt(agent_name: str, vertical: dict) -> str:
    env = Environment(loader=FileSystemLoader(".claude/agents"))
    template = env.get_template(f"{agent_name}.md.j2")
    return template.render(vertical=vertical)
```

### Template variables needed in select.md

```jinja2
**Tiers:**
{% for tier in vertical.tiers %}
- `{{ tier.key }}` ({{ tier.min_items }}+ stories): {{ vertical.tier_definitions[tier.key] }}
{% endfor %}
- `signals`: {{ vertical.tier_definitions.signals }}

**Sections:** {{ vertical.sections | map(attribute='key') | join(', ') }}

**Editorial rules:**
{{ vertical.editorial_rules }}

**Output schema:**
{
  {% for tier in vertical.tiers %}
  "{{ tier.key }}": [{"cluster_index": 0, "region": "{{ vertical.sections[0].key }}", "article_ids": ["A1"]}],
  {% endfor %}
  "signals": {
    {% for section in vertical.sections %}
    "{{ section.key }}": []{% if not loop.last %},{% endif %}
    {% endfor %}
  }
}
```

### Template variables needed in write.md

```jinja2
**Writing style:**
{{ vertical.style_block }}

**Preheader:** {{ vertical.preheader_instructions }}

**Output schema:**
  "signals": {
    {% for section in vertical.sections %}
    "{{ section.key }}": []{% if not loop.last %},{% endif %}
    {% endfor %}
  }
```

The `reporting_varies` block in write.md should be conditionally included:

```jinja2
{% if vertical.show_reporting_varies %}
**Reporting varies ({{ vertical.tiers[0].key }} only, optional):** ...
{% endif %}
```

---

## 4. Minimal Change Set (20% -> 80%)

If the goal is to run ONE new vertical (say, ML papers) against the existing infrastructure, the following changes get you there without touching the Rust server, email infrastructure, or pipeline orchestration:

### Step 1: Add `vertical.json` (new file, ~50 lines)

Define tiers, sections, editorial rules, and style block for the new vertical.

### Step 2: Make `mcp_server.py` generate its schema dynamically (~30 lines changed)

`SIGNALS_SCHEMA` and `SELECTIONS_SCHEMA` are the hard blockers -- they will reject any output with non-news region keys. Load the vertical config at server startup and generate these dicts programmatically from `vertical["sections"]` and `vertical["tiers"]`.

```python
# Instead of hardcoded SIGNALS_SCHEMA properties:
def build_signals_schema(sections):
    return {
        "type": "object",
        "properties": {s["key"]: {"type": "array", "items": SIGNAL_SCHEMA} for s in sections},
        "required": [s["key"] for s in sections],
        "additionalProperties": False,
    }
```

This is the single highest-leverage change. Without it, Claude's output is rejected by schema validation regardless of what the prompts say.

### Step 3: Convert `select.md` and `write.md` to Jinja2 templates (~20 lines of template syntax added)

Inject tier definitions, section keys, and editorial rules. The structure of both prompts stays the same; only the domain-specific blocks change.

### Step 4: Make `render.py` read `REGION_CONFIG` and `REGION_ORDER` from vertical config

Replace the two hardcoded dicts (lines 19-28) with something loaded from config. All downstream functions in render.py that iterate `REGION_ORDER` will then work correctly without further changes.

### Step 5: Pass vertical config into `prepare.py` for source CSV columns

The sources CSV currently always writes `bias` and `factuality`. For an ML vertical these would be `institution` and `venue`. The column list should come from `vertical["source_metadata_fields"]`. The article index builder (`article_index[article_id]`) also hardcodes `bias` -- this needs to read from source metadata generically.

### What this does NOT yet solve (acceptable for V1)

- The HTML template (`digest-template.html`) still has `{{MUST_KNOW}}`, `{{SHOULD_KNOW}}`, `{{SIGNALS}}` placeholders -- a vertical with different tier names needs its own template. For V1, accept this constraint: keep a `templates/<vertical_id>/` directory.
- The `/sources` page in circulation renders the bias spectrum. It will look odd for non-news verticals, but it won't break.
- `render_article()` always renders a `bias` label in the sources line. For a non-news vertical this would be blank/wrong. A `show_source_bias` flag in vertical config controls whether to render that column.

---

## 5. What Stays Fixed

These components need zero changes to support multiple verticals. They are genuinely content-agnostic:

- **Pipeline orchestration** (`run.py`): fetch -> dedup -> Claude -> render -> email. The sequence is universal.
- **RSS fetch and dedup** (`feeds.py`, `dedup.py`): TF-IDF dedup on titles works for any text content source. Academic paper feeds are still RSS.
- **Database schema and migrations**: `digest_runs`, `shown_narratives`, `source_health`, `digests`, `run_usage` are all generic. No tier names are stored in the schema -- `shown_narratives.tier` is a string, so "breakthrough" works as-is.
- **MCP protocol machinery** (`mcp_server.py` main loop, validation harness): only the schema objects need to change, not the server itself.
- **CLUSTER agent** (`cluster.md`): groups items covering the same story -- entirely content-agnostic.
- **RECAP agent** (`recap.md`): summarises recent titles -- no domain knowledge.
- **COHERENCE agent** (`coherence.md`): checks headlines against source text -- no domain knowledge.
- **Dispatcher** (`news-digest-select.md`): orchestration logic is structural. Steps 1-5 are valid for any vertical. Only the output key names referenced in verification need updating.
- **Email delivery** (`broadcast.py`): sends HTML. Doesn't care about content.
- **Usage tracking** (`usage.py`): parses Claude session JSONL. Content-agnostic.
- **CSS processing** in `render.py`: `minify_css()`, `resolve_css_variables()`, `prepare_for_email()` are generic.
- **Circulation server** (`circulation/`): serves HTML blobs from the database. Almost entirely content-agnostic (see Section 7 for the one exception).

---

## 6. Migration Path

The goal is to add multi-vertical support without breaking the current news digest in production. The news digest is the only vertical today, so it must continue to be the implicit default.

### Phase 0: No-op abstraction (safe, no behaviour change)

Extract `REGION_CONFIG` and `REGION_ORDER` from `render.py` into a `vertical.json` file in the repo root (or `newsroom/`), and load them at startup. The news digest vertical is the only file; behaviour is identical. This is a refactor, not a feature, and can be deployed as a normal patch.

At the same time, convert `select.md` and `write.md` to `.j2` templates with the current values baked in as template defaults. If the rendering path is absent (no Jinja2 available), fall back to reading the raw file. Deploy and verify the digest runs correctly.

### Phase 1: Parameterise the MCP schema (one breaking risk)

Make `mcp_server.py` read `vertical.json` and generate `SIGNALS_SCHEMA` and `SELECTIONS_SCHEMA` dynamically. This is the riskiest change because a schema regression causes Claude to retry indefinitely. Mitigate by:

1. Adding a unit test that generates the schema from the current news config and asserts it is identical to the hardcoded version.
2. Deploying behind `--dry-run` first and inspecting `selections.json`.

The news digest config produces exactly the same schema it does today. No production risk once the test passes.

### Phase 2: Second vertical (validation)

Add `vertical-ml-papers.json`. Pass `--vertical ml-papers` as a run flag. The pipeline reads the right config, renders the right prompts, uses the right schema, and outputs a digest. Run with `--dry-run --no-record` until output quality is acceptable.

At this point no shared state is affected: each vertical uses the same DB tables (tier is a string), the same email infrastructure, and the same codebase. They are differentiated only by which config file is loaded.

### Phase 3: Per-vertical HTML templates

Create `newsroom/templates/<vertical_id>/digest-template.html` for each vertical. The `TEMPLATE_FILE` config path is already an env var path; add a `--vertical` flag that selects the subdirectory. The current template stays untouched.

### Phase 4: Multiple simultaneous verticals (optional)

If the goal is to run both a news digest and an ML papers digest on the same cron schedule, each becomes its own Docker Compose service with its own `VERTICAL` env var and its own DB path. The image is shared; the config is injected. No code changes needed beyond Phase 2-3.

### What NOT to do

- Do not try to generalise the HTML template with dynamic tier placeholders. A per-vertical template directory is simpler, more maintainable, and avoids a Jinja2 blast radius in the render path.
- Do not refactor `run.py` into a plugin architecture. The 296-line god function is an annoyance but not a blocker for multi-vertical. Refactor it when the second vertical is running cleanly, not before.
- Do not add vertical routing to the circulation server until there are actually two verticals running. Premature multi-tenancy in Rust is expensive.

---

## 7. Rust Circulation Impact

The Rust server (`circulation/`) serves HTML blobs stored in the `digests` table. It is largely content-agnostic. The areas that would need attention for a second vertical:

### No change required

- Index page (`/`): lists digests by date, shows preheader text. Works for any vertical.
- Digest serving (`/{date}`): serves stored HTML. Works for any vertical.
- Subscribe/unsubscribe flows: content-agnostic.
- Stats page (`/stats`): queries `source_health`, `digest_runs`, `shown_narratives` -- all generic.
- Health endpoint: generic.

### Would need changes for multi-vertical

- **`/sources` page** (`circulation/src/templates.rs`): renders a bias colour spectrum (far-left to far-right) and factuality badges. This is hardcoded news journalism metadata. For a non-news vertical it renders nonsense. The fix: make the sources page conditional on `content_type` from config, or suppress the spectrum/badges when `show_source_bias = false`. This is cosmetic -- it does not break serving digests.
- **Multi-tenancy routing**: if two verticals share one circulation server, you'd need namespaced routes (`/news/2026-04-03`, `/ml-papers/2026-04-03`) or separate server instances. Separate instances (separate Docker services, separate ports behind a reverse proxy) are far simpler and recommended for the initial multi-vertical deployment.
- **`sources.json` embedded via `include_str!`** (`circulation/Dockerfile`): the binary bakes in one sources file at compile time. For a second vertical with different sources, you'd need a second binary or a runtime sources path. The symlink `circulation/sources.json -> ../newsroom/sources.json` already handles local builds; the Docker build context would need a per-vertical sources file injected at build time. This is a Dockerfile concern, not a code concern.

### Summary

The circulation server is safe to leave untouched for a second vertical if the second vertical runs as a separate service instance. The `/sources` page bias spectrum is the only aesthetic issue, and it is non-breaking.
