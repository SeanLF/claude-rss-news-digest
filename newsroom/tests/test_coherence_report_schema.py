"""The coherence report schema used by the structured-output arm constrains SHAPE only.

2026-08-21: a `minItems: 12` on a six-claim audit made the model invent six verdicts. A grammar
guarantees count, never correspondence, so this schema must never carry a count."""

import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import schema


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def test_schema_carries_no_count_constraint():
    assert not {"minItems", "maxItems", "minLength", "maxLength"} & set(_keys(schema.COHERENCE_REPORT_SCHEMA))


def test_schema_accepts_a_shipped_report_shape():
    report = {
        "results": [
            {"headline": "H1", "article_ids": ["A1", "A2"], "pass": True, "reason": "ok"},
            {
                "headline": "H2",
                "article_ids": ["A5"],
                "pass": False,
                "reason": "summary: x",
                "failed_fields": ["summary"],
                "failure_kinds": {"summary": "contradicted"},
            },
        ]
    }
    Draft7Validator.check_schema(schema.COHERENCE_REPORT_SCHEMA)
    assert not list(Draft7Validator(schema.COHERENCE_REPORT_SCHEMA).iter_errors(report))


def test_schema_rejects_an_unknown_field_name_and_kind():
    bad = {
        "results": [
            {"headline": "H", "article_ids": [], "pass": False, "reason": "r", "failed_fields": ["title"]},
            {
                "headline": "H",
                "article_ids": [],
                "pass": False,
                "reason": "r",
                "failure_kinds": {"summary": "fabricated"},
            },
        ]
    }
    errors = list(Draft7Validator(schema.COHERENCE_REPORT_SCHEMA).iter_errors(bad))
    assert len(errors) == 2


def test_schema_is_json_serialisable_for_the_cli_flag():
    json.dumps(schema.COHERENCE_REPORT_SCHEMA)
