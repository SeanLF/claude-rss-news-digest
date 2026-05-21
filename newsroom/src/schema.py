"""JSON schema for the curated selections payload.

Used by the post-dispatcher merge step to validate the assembled selections.json
before downstream rendering.
"""

from typing import Any

from jsonschema import Draft7Validator

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
    },
    "required": ["headline", "summary", "why_it_matters", "sources"],
    "additionalProperties": False,
}

SIGNAL_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "source": SOURCE_SCHEMA,
    },
    "required": ["headline", "source"],
    "additionalProperties": False,
}

SIGNALS_SCHEMA = {
    "type": "object",
    "properties": {
        "americas": {"type": "array", "items": SIGNAL_SCHEMA},
        "europe": {"type": "array", "items": SIGNAL_SCHEMA},
        "asia_pacific": {"type": "array", "items": SIGNAL_SCHEMA},
        "middle_east_africa": {"type": "array", "items": SIGNAL_SCHEMA},
        "tech": {"type": "array", "items": SIGNAL_SCHEMA},
    },
    "required": ["americas", "europe", "asia_pacific", "middle_east_africa", "tech"],
    "additionalProperties": False,
}

SELECTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "must_know": {"type": "array", "items": ARTICLE_SCHEMA},
        "should_know": {"type": "array", "items": ARTICLE_SCHEMA},
        "signals": SIGNALS_SCHEMA,
        "preheader": {"type": "string", "maxLength": 150},
    },
    "required": ["must_know", "should_know", "signals", "preheader"],
    "additionalProperties": False,
}


def validate_selections(payload: Any) -> list[str]:
    """Validate payload against SELECTIONS_SCHEMA. Returns list of error messages."""
    validator = Draft7Validator(SELECTIONS_SCHEMA)
    errors = []
    for error in sorted(validator.iter_errors(payload), key=lambda e: e.path):
        path = ".".join(str(p) for p in error.path) or "(root)"
        if error.validator == "type":
            errors.append(f"{path}: expected {error.validator_value}, got {type(error.instance).__name__}")
        else:
            errors.append(f"{path}: {error.message}")
    return errors
