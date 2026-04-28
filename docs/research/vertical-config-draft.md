# Vertical Config Draft -- News Digest Generalisation

Research artifact. Do not commit without review.

---

## How this was derived

All hardcoded values were extracted from:
- `newsroom/src/mcp_server.py` -- SIGNALS_SCHEMA, SELECTIONS_SCHEMA, TOOLS description
- `newsroom/src/render.py` -- REGION_CONFIG, REGION_ORDER, tier references in extract_headlines / render_digest / calculate_reading_time
- `newsroom/src/digest.py` -- tier keys in load_selections, resolve_article_ids, write_digest
- `.claude/agents/select.md` -- tier definitions, region list, editorial rules
- `.claude/agents/write.md` -- writing style, field rules, preheader spec

---

## Part 1: vertical.json for news digest

This is the "current" vertical. Running the generalised system with this file must produce
schemas and behaviour identical to the current hardcoded implementation.

```json
{
  "id": "news-digest",
  "display_name": "News Digest",
  "content_type": "world_news",

  "tiers": [
    {
      "key": "must_know",
      "display_name": "Must Know",
      "min_items": 3,
      "include_why_it_matters": true,
      "include_reporting_varies": true,
      "description": "3+ major stories you'd be embarrassed not to know"
    },
    {
      "key": "should_know",
      "display_name": "Should Know",
      "min_items": 5,
      "include_why_it_matters": true,
      "include_reporting_varies": false,
      "description": "5+ important but not urgent stories"
    },
    {
      "key": "signals",
      "display_name": "Signals",
      "min_items": 0,
      "include_why_it_matters": false,
      "include_reporting_varies": false,
      "is_signals_tier": true,
      "description": "One-liner signals clustered by section"
    }
  ],

  "sections": [
    {"key": "americas",          "display_name": "Americas",           "emoji": "🌎"},
    {"key": "europe",            "display_name": "Europe",             "emoji": "🌍"},
    {"key": "asia_pacific",      "display_name": "Asia-Pacific",       "emoji": "🌏"},
    {"key": "middle_east_africa","display_name": "Middle East & Africa","emoji": "🌍"},
    {"key": "tech",              "display_name": "Tech",               "emoji": "🤖"}
  ],

  "source_metadata_fields": ["name", "url", "bias", "factuality", "perspective"],
  "show_source_bias": true,
  "show_reporting_varies": true,

  "preheader": {
    "max_length": 150,
    "description": "One-sentence preview of today's digest for email inbox preview and the archive index page. Capture the 2-3 most significant stories. No links."
  },

  "editorial_rules": {
    "interest_priorities": {
      "high":   ["geopolitics", "tech/AI", "privacy/surveillance"],
      "medium": ["economic policy", "France/Canada specific"],
      "filter": ["celebrity", "sports", "lifestyle", "US domestic (unless affects other countries)"]
    },
    "balance_instruction": "When a dominant story consumes the news cycle, actively ensure the digest still covers other regions and topics. Prioritise breadth across regions and subject areas.",
    "continuity_instruction": "Reference recap and weekly_recap. Only re-cover a story from yesterday if there is a specific new fact, decision, or consequence not available yesterday.",
    "comprehensiveness": "Include more stories rather than fewer."
  },

  "writing_style": {
    "voice": "The Economist meets AP wire",
    "rules": [
      "Short sentences, short words",
      "Lead with most important fact",
      "Be specific: '12 killed' not 'many casualties'",
      "Hedge unverified claims: 'reportedly', 'according to'",
      "No journalese: 'sparked concerns', 'sent shockwaves', 'slammed'",
      "No sensationalism: 'explosive', 'shocking', 'unprecedented'",
      "No editorializing: report facts, let reader judge"
    ],
    "headline_rules": "Sentence case. Active voice. Key actor + action.",
    "summary_rules": "2-3 sentences max. First = the news (who did what). Second = context. Do not fabricate beyond what is in the article summaries.",
    "why_it_matters_rules": "One sentence that identifies a specific mechanism, contradiction, or second-order consequence. Name a concrete cause-and-effect chain or reveal an irony the reader would not see from the headline alone.",
    "reporting_varies_rules": "Only when sources genuinely frame the story differently. 2-3 perspectives max. Skip if all sources report it the same way. Must_know only, optional.",
    "signals_rules": "One-liner headline + one article_id per signal."
  },

  "tier_definitions": {
    "must_know": "Stories you'd be embarrassed not to know. Major geopolitical shifts, significant deaths, major policy changes.",
    "should_know": "Important but not urgent. Developing situations, notable policy moves, significant tech announcements.",
    "signals": "One-liners worth tracking. Everything noteworthy that didn't make the tiers above. Grouped by region."
  }
}
```

---

## Part 2: Dynamic schema generation

Replace the hardcoded `SIGNALS_SCHEMA` and `SELECTIONS_SCHEMA` constants in
`newsroom/src/mcp_server.py` with these functions. When called with the news digest
vertical config loaded from Part 1, the output must be structurally identical to the
current hardcoded schemas.

```python
"""
Dynamic schema generation for MCP tool input schemas.

These functions replace the hardcoded SIGNALS_SCHEMA and SELECTIONS_SCHEMA
constants. The vertical config dict is the parsed content of vertical.json.
"""


def build_signals_schema(vertical: dict) -> dict:
    """Build the signals tier JSON schema from vertical config.

    The signals tier is a dict keyed by section, each containing an array
    of signal objects (headline + single source). Sections and their order
    are defined by vertical["sections"].

    The output, when called with the news-digest vertical, is identical to:

        SIGNALS_SCHEMA = {
            "type": "object",
            "properties": {
                "americas": {"type": "array", "items": SIGNAL_SCHEMA},
                "europe": {"type": "array", "items": SIGNAL_SCHEMA},
                ...
            },
            "required": ["americas", "europe", ...],
            "additionalProperties": False,
            "description": "One-liner signals clustered by region",
        }
    """
    section_keys = [s["key"] for s in vertical["sections"]]

    return {
        "type": "object",
        "properties": {
            key: {"type": "array", "items": SIGNAL_SCHEMA}
            for key in section_keys
        },
        "required": section_keys,
        "additionalProperties": False,
        "description": "One-liner signals clustered by region",
    }


def build_article_schema(tier_config: dict) -> dict:
    """Build a single article object schema for a given tier config.

    A tier config dict has keys: key, include_why_it_matters,
    include_reporting_varies. The base ARTICLE_SCHEMA is extended or
    narrowed accordingly.

    For the current news digest, must_know and should_know both include
    why_it_matters, so the base ARTICLE_SCHEMA is used unchanged for both.
    The reporting_varies field is always permitted in the schema (it is
    optional/nullable); include_reporting_varies controls rendering only.
    """
    # Base properties shared by all article tiers
    properties = {
        "headline": {"type": "string", "description": "Headline in sentence case"},
        "summary": {"type": "string", "description": "2-3 sentence summary"},
        "sources": {"type": "array", "items": SOURCE_SCHEMA, "minItems": 1},
    }
    required = ["headline", "summary", "sources"]

    if tier_config.get("include_why_it_matters", False):
        properties["why_it_matters"] = {
            "type": "string",
            "description": "1-2 sentence insight on significance",
        }
        required.append("why_it_matters")

    if tier_config.get("include_reporting_varies", False):
        properties["reporting_varies"] = {
            "type": "array",
            "items": REPORTING_VARIES_SCHEMA,
            "description": "Optional - only for stories with divergent framing",
        }
        # reporting_varies is optional -- not added to required

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def build_selections_schema(vertical: dict) -> dict:
    """Build the top-level write_selections input schema from vertical config.

    Iterates tiers defined in vertical["tiers"]. Tiers with is_signals_tier=true
    are rendered using build_signals_schema instead of build_article_schema.
    Adds the preheader field using vertical["preheader"] config.

    The output, when called with the news-digest vertical, is identical to the
    current hardcoded SELECTIONS_SCHEMA.
    """
    properties = {}
    required = []

    for tier in vertical["tiers"]:
        key = tier["key"]
        required.append(key)

        if tier.get("is_signals_tier", False):
            properties[key] = build_signals_schema(vertical)
        else:
            article_schema = build_article_schema(tier)
            properties[key] = {
                "type": "array",
                "items": article_schema,
                "description": tier.get("description", ""),
            }

    # Preheader is always a top-level field
    preheader_cfg = vertical.get("preheader", {})
    preheader_schema = {
        "type": "string",
        "description": preheader_cfg.get("description", "One-sentence digest preview."),
    }
    if "max_length" in preheader_cfg:
        preheader_schema["maxLength"] = preheader_cfg["max_length"]

    properties["preheader"] = preheader_schema
    required.append("preheader")

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def build_tools(vertical: dict, selections_schema: dict) -> list[dict]:
    """Build the MCP TOOLS list from vertical config and generated schema.

    The tool description references tier names and counts drawn from the
    vertical config rather than hardcoded strings.
    """
    # Build description from tier configs
    tier_lines = []
    for tier in vertical["tiers"]:
        if tier.get("is_signals_tier"):
            tier_lines.append("signals grouped by region")
        elif tier.get("min_items", 0) > 0:
            tier_lines.append(f"{tier['key']} ({tier['min_items']}+ stories)")
        else:
            tier_lines.append(tier["key"])

    tier_description = ", ".join(tier_lines)

    return [
        {
            "name": "write_selections",
            "title": f"Write {vertical['display_name']} Selections",
            "description": (
                f"Writes the complete curated {vertical['display_name'].lower()} digest to selections.json. "
                "Call this after reading ALL article files and making editorial selections. "
                f"The selections object must include {tier_description}, "
                "and a preheader sentence for inbox preview. "
                "Do NOT call until you have processed every article file. "
                "Schema validation will reject incomplete or malformed input - fix errors and retry."
            ),
            "inputSchema": selections_schema,
        }
    ]
```

### Schema constants that remain static

These do not depend on vertical config and stay as module-level constants:

```python
SOURCE_SCHEMA = {
    "type": "object",
    "properties": {
        "article_id": {
            "type": "string",
            "description": "Article ID from articles CSV (e.g., 'A1', 'A42')",
        },
    },
    "required": ["article_id"],
    "additionalProperties": False,
}

REPORTING_VARIES_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string"},
        "angle": {"type": "string"},
        "bias": {"type": "string"},
    },
    "required": ["source", "angle", "bias"],
    "additionalProperties": False,
}

SIGNAL_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "Brief headline with key fact"},
        "source": SOURCE_SCHEMA,
    },
    "required": ["headline", "source"],
    "additionalProperties": False,
}
```

---

## Part 3: Vertical config loading code

Place this near the top of `mcp_server.py`, after imports and before schema constants.

```python
import os
import json
from pathlib import Path


def load_vertical(path: str | Path | None = None) -> dict:
    """Load vertical config from a JSON file.

    Resolution order:
    1. Explicit path argument (for testing / overrides)
    2. VERTICAL_CONFIG_PATH env var
    3. vertical.json in the current working directory

    Raises:
        RuntimeError: If the file is missing or unparseable
    """
    if path is None:
        env_path = os.environ.get("VERTICAL_CONFIG_PATH")
        path = env_path if env_path else "vertical.json"

    path = Path(path)
    if not path.exists():
        raise RuntimeError(
            f"Vertical config not found: {path}. "
            "Set VERTICAL_CONFIG_PATH env var or place vertical.json in the working directory."
        )

    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Vertical config is invalid JSON ({path}): {e}") from e


# Module-level initialisation -- runs once at server start
_vertical = load_vertical()
SELECTIONS_SCHEMA = build_selections_schema(_vertical)
TOOLS = build_tools(_vertical, SELECTIONS_SCHEMA)
```

### Render.py equivalents

`REGION_CONFIG` and `REGION_ORDER` in `render.py` should also be derived from the loaded
vertical. The render module currently imports nothing from outside `src/`, so vertical
loading should be extracted to a shared `config.py` helper or passed in from `run.py`.
A minimal approach that avoids circular imports:

```python
# In render.py, replace the two constants with a loader:

def _load_section_config() -> tuple[dict, list]:
    """Load section display config from vertical.json.

    Returns:
        (REGION_CONFIG dict mapping key -> (display_name, emoji),
         REGION_ORDER list of keys in display order)
    """
    vertical = load_vertical()  # same function as mcp_server.py
    region_config = {
        s["key"]: (s["display_name"], s["emoji"])
        for s in vertical["sections"]
    }
    region_order = [s["key"] for s in vertical["sections"]]
    return region_config, region_order


REGION_CONFIG, REGION_ORDER = _load_section_config()
```

The tier names in `digest.py` (`must_know`, `should_know`) are used as dict keys that
must match the keys in `selections.json` (written by Claude via MCP). They should also be
derived from the vertical config in the generalised system. A minimal change:

```python
# In digest.py load_selections, replace hardcoded tier checks:
def load_selections(selections_file: Path, vertical: dict) -> dict:
    ...
    for tier in vertical["tiers"]:
        if tier.get("is_signals_tier"):
            sections = [s["key"] for s in vertical["sections"]]
            signals_count = sum(len(selections.get("signals", {}).get(k, [])) for k in sections)
        elif tier.get("min_items", 0) > 0:
            count = len(selections.get(tier["key"], []))
            if count < tier["min_items"]:
                logger.warning(
                    "Only %d %s stories (expected %d+)", count, tier["key"], tier["min_items"]
                )
```

---

## Part 4: Regression test sketch

```python
"""
Regression test: vertical.json schema generation must produce output
identical to the previously hardcoded SIGNALS_SCHEMA and SELECTIONS_SCHEMA.

Run with: pytest newsroom/tests/test_vertical_schema.py
"""
import json
from pathlib import Path

import pytest

# -- Reference: exact copies of the hardcoded schemas from mcp_server.py at
#    the time this test was written. If these ever diverge from the live
#    constants before migration, update these references, not the test logic.

SOURCE_SCHEMA = {
    "type": "object",
    "properties": {
        "article_id": {
            "type": "string",
            "description": "Article ID from articles CSV (e.g., 'A1', 'A42')",
        },
    },
    "required": ["article_id"],
    "additionalProperties": False,
}

REPORTING_VARIES_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string"},
        "angle": {"type": "string"},
        "bias": {"type": "string"},
    },
    "required": ["source", "angle", "bias"],
    "additionalProperties": False,
}

ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "Headline in sentence case"},
        "summary": {"type": "string", "description": "2-3 sentence summary"},
        "why_it_matters": {"type": "string", "description": "1-2 sentence insight on significance"},
        "sources": {"type": "array", "items": SOURCE_SCHEMA, "minItems": 1},
        "reporting_varies": {
            "type": "array",
            "items": REPORTING_VARIES_SCHEMA,
            "description": "Optional - only for stories with divergent framing",
        },
    },
    "required": ["headline", "summary", "why_it_matters", "sources"],
    "additionalProperties": False,
}

SIGNAL_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "Brief headline with key fact"},
        "source": SOURCE_SCHEMA,
    },
    "required": ["headline", "source"],
    "additionalProperties": False,
}

SIGNALS_SCHEMA_REFERENCE = {
    "type": "object",
    "properties": {
        "americas":           {"type": "array", "items": SIGNAL_SCHEMA},
        "europe":             {"type": "array", "items": SIGNAL_SCHEMA},
        "asia_pacific":       {"type": "array", "items": SIGNAL_SCHEMA},
        "middle_east_africa": {"type": "array", "items": SIGNAL_SCHEMA},
        "tech":               {"type": "array", "items": SIGNAL_SCHEMA},
    },
    "required": ["americas", "europe", "asia_pacific", "middle_east_africa", "tech"],
    "additionalProperties": False,
    "description": "One-liner signals clustered by region",
}

SELECTIONS_SCHEMA_REFERENCE = {
    "type": "object",
    "properties": {
        "must_know": {
            "type": "array",
            "items": ARTICLE_SCHEMA,
            "description": "3+ major stories you'd be embarrassed not to know",
        },
        "should_know": {
            "type": "array",
            "items": ARTICLE_SCHEMA,
            "description": "5+ important but not urgent stories",
        },
        "signals": SIGNALS_SCHEMA_REFERENCE,
        "preheader": {
            "type": "string",
            "maxLength": 150,
            "description": (
                "One-sentence preview of today's digest for email inbox preview and the archive index page. "
                "Capture the 2-3 most significant stories. No links."
            ),
        },
    },
    "required": ["must_know", "should_know", "signals", "preheader"],
    "additionalProperties": False,
}


# -- Fixtures

@pytest.fixture(scope="module")
def vertical() -> dict:
    """Load the news digest vertical.json from the repo root."""
    vertical_path = Path(__file__).parents[3] / "vertical.json"
    assert vertical_path.exists(), f"vertical.json not found at {vertical_path}"
    return json.loads(vertical_path.read_text())


# -- Tests

def test_signals_schema_matches_reference(vertical):
    """build_signals_schema output must be identical to the hardcoded SIGNALS_SCHEMA."""
    from mcp_server import build_signals_schema, SIGNAL_SCHEMA as _SIGNAL_SCHEMA

    result = build_signals_schema(vertical)
    assert result == SIGNALS_SCHEMA_REFERENCE, (
        "Generated signals schema differs from reference.\n"
        f"Got:      {json.dumps(result, indent=2)}\n"
        f"Expected: {json.dumps(SIGNALS_SCHEMA_REFERENCE, indent=2)}"
    )


def test_selections_schema_matches_reference(vertical):
    """build_selections_schema output must be identical to the hardcoded SELECTIONS_SCHEMA."""
    from mcp_server import build_selections_schema

    result = build_selections_schema(vertical)
    assert result == SELECTIONS_SCHEMA_REFERENCE, (
        "Generated selections schema differs from reference.\n"
        f"Got:      {json.dumps(result, indent=2)}\n"
        f"Expected: {json.dumps(SELECTIONS_SCHEMA_REFERENCE, indent=2)}"
    )


def test_signals_schema_section_order(vertical):
    """Section order in signals schema must match vertical.json sections order."""
    from mcp_server import build_signals_schema

    result = build_signals_schema(vertical)
    expected_keys = [s["key"] for s in vertical["sections"]]
    assert result["required"] == expected_keys
    assert list(result["properties"].keys()) == expected_keys


def test_tier_min_items_warnings_config(vertical):
    """Every non-signals tier with min_items > 0 must be present in vertical.json."""
    tiers_with_mins = [
        (t["key"], t["min_items"])
        for t in vertical["tiers"]
        if not t.get("is_signals_tier") and t.get("min_items", 0) > 0
    ]
    # Regression: must_know >= 3, should_know >= 5
    assert ("must_know", 3) in tiers_with_mins
    assert ("should_know", 5) in tiers_with_mins


def test_vertical_sections_match_render_constants(vertical):
    """REGION_CONFIG and REGION_ORDER in render.py must match vertical.json sections."""
    import sys
    sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
    from render import REGION_CONFIG, REGION_ORDER

    expected_order = [s["key"] for s in vertical["sections"]]
    assert REGION_ORDER == expected_order, (
        f"REGION_ORDER {REGION_ORDER} does not match vertical.json sections {expected_order}"
    )

    for section in vertical["sections"]:
        key = section["key"]
        assert key in REGION_CONFIG, f"Section {key} missing from REGION_CONFIG"
        config_name, config_emoji = REGION_CONFIG[key]
        assert config_name == section["display_name"], (
            f"REGION_CONFIG[{key}] display_name mismatch: {config_name!r} vs {section['display_name']!r}"
        )
        assert config_emoji == section["emoji"], (
            f"REGION_CONFIG[{key}] emoji mismatch: {config_emoji!r} vs {section['emoji']!r}"
        )
```

---

## Implementation notes

### Precision issues to resolve before coding

1. **ARTICLE_SCHEMA currently has `reporting_varies` in properties for both tiers**, even
   though `include_reporting_varies=false` for `should_know`. The schema permits it;
   rendering ignores it. `build_article_schema` above replicates this: the field appears
   in `properties` but not in `required` only when `include_reporting_varies=true`. This
   means the generated `should_know` schema currently does NOT include `reporting_varies`
   in its properties -- which is a subtle difference from the hardcoded `ARTICLE_SCHEMA`
   that is shared by both tiers.

   **Resolution for regression test to pass:** Use a single shared article schema for
   both tiers (as today), or adjust `build_article_schema` to always include
   `reporting_varies` in properties (as optional) regardless of the flag. The flag then
   only controls whether the field is listed in `required`. The test reference above
   uses the current shared `ARTICLE_SCHEMA`, so the generator must also produce the same
   schema for both `must_know` and `should_know`.

   Simplest fix: if any tier has `include_reporting_varies=true`, build the article schema
   with `reporting_varies` in properties (optional) and share it across tiers. Gate
   `include_reporting_varies` on whether ANY tier uses it, not per-tier.

2. **Section order in `REGION_ORDER` vs `vertical.json` sections**: the current code
   lists Americas first ("where subscribers are"). The vertical.json sections array must
   preserve that order explicitly -- it is the canonical ordering.

3. **`vertical.json` placement**: The file should live at the repo root (next to
   `newsroom/`, `circulation/`), not inside `newsroom/`. The default path in `load_vertical`
   resolves relative to `cwd`, which in Docker is `/app`. Place `vertical.json` at
   `/app/vertical.json` and mount or COPY it there, or set `VERTICAL_CONFIG_PATH` in the
   environment.

4. **`mcp_server.py` is standalone** (no `src/` imports). The `load_vertical` function
   must be self-contained or copied into `mcp_server.py` -- it cannot import from `render`
   or `config`. `render.py` will need its own copy of the loader or a shared utility.
