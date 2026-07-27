"""JSON schema for the curated selections payload.

Used by the post-dispatcher merge step to validate the assembled selections.json
before downstream rendering.
"""

from typing import Any

from jsonschema import Draft7Validator

# Cap on the not_covered_blurb footer garnish. 300 was borrowed from the preheader cap, where the
# number is a real external constraint (inbox preview length). Nothing external bounds this footer,
# and the borrowed number bit: over the 26 digests since the blurb shipped 2026-07-02, SELECT wrote
# 303-463 chars and 62% of digests shipped a footer truncated mid-clause ("...didn't clear the…").
# 500 clears every observed blurb with headroom. The cap is kept, not removed -- it still bounds a
# reader-facing string built from model output -- but it is now sized from what SELECT actually
# writes. Named rather than inlined because merge enforces it before validation reaches it.
NOT_COVERED_BLURB_MAX_LEN = 500

SOURCE_SCHEMA = {
    "type": "object",
    "properties": {
        "article_id": {"type": "string", "description": "Article ID from articles CSV (e.g., 'A1', 'A42')"},
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
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "why_it_matters": {"type": "string"},
        "sources": {"type": "array", "items": SOURCE_SCHEMA, "minItems": 1},
        "reporting_varies": {"type": "array", "items": REPORTING_VARIES_SCHEMA},
        # Optional CLUSTER story label, attached by merge.assemble_selections for
        # redundancy/overlap tracking. Absent when no cluster could be mapped.
        "cluster_id": {"type": "string"},
    },
    "required": ["headline", "summary", "why_it_matters", "sources"],
    "additionalProperties": False,
}

SELECTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "must_know": {"type": "array", "items": ARTICLE_SCHEMA},
        "should_know": {"type": "array", "items": ARTICLE_SCHEMA},
        # 150 is the editorial target WRITE is given; the schema tolerates a small
        # (<=5%) overshoot so a couple extra chars never abort a delivered digest.
        # This maxLength is the single source of truth for the cap --
        # merge._enforce_capped_string_fields reads it and word-boundary-truncates
        # anything beyond it before validation, for this and every capped field.
        "preheader": {"type": "string", "maxLength": 157},
        # Optional SELECT-stage garnish (what was deliberately filtered and why),
        # copied through by merge.assemble_selections. Absent when SELECT's
        # selected.json has no usable field -- see _load_not_covered_blurb.
        "not_covered_blurb": {"type": "string", "maxLength": NOT_COVERED_BLURB_MAX_LEN},
    },
    "required": ["must_know", "should_know", "preheader"],
    "additionalProperties": False,
}


def validate_selections(payload: Any) -> list[str]:
    """Validate payload against SELECTIONS_SCHEMA. Returns list of error messages."""
    validator = Draft7Validator(SELECTIONS_SCHEMA)
    errors = []
    for error in sorted(validator.iter_errors(payload), key=lambda e: [str(p) for p in e.path]):
        path = ".".join(str(p) for p in error.path) or "(root)"
        if error.validator == "type":
            errors.append(f"{path}: expected {error.validator_value}, got {type(error.instance).__name__}")
        else:
            errors.append(f"{path}: {error.message}")
    return errors
