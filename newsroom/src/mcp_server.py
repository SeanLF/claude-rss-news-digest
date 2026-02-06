#!/usr/bin/env python3
"""MCP server for news digest - provides structured tool for selections output."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator


def log(msg: str):
    """Log to stderr (visible in parent process logs)."""
    ts = datetime.now(UTC).strftime("%H:%M:%S")
    print(f"[MCP {ts}] {msg}", file=sys.stderr, flush=True)


# MCP protocol over stdio
def send_response(id: Any, result: Any = None, error: Any = None):
    """Send JSON-RPC response."""
    response = {"jsonrpc": "2.0", "id": id}
    if error:
        response["error"] = error
    else:
        response["result"] = result
    print(json.dumps(response), flush=True)


def send_notification(method: str, params: Any = None):
    """Send JSON-RPC notification."""
    msg = {"jsonrpc": "2.0", "method": method}
    if params:
        msg["params"] = params
    print(json.dumps(msg), flush=True)


# Schema definitions - additionalProperties: false rejects unknown fields
SOURCE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Source name (e.g., 'Wall Street Journal')"},
        "url": {"type": "string", "description": "Article URL"},
        "bias": {"type": "string", "enum": ["left", "center-left", "center", "center-right", "right"]},
    },
    "required": ["name", "url", "bias"],
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
    "description": "One-liner signals clustered by region",
}

SELECTIONS_SCHEMA = {
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
        "signals": SIGNALS_SCHEMA,
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

TOOLS = [
    {
        "name": "write_selections",
        "title": "Write News Selections",
        "description": (
            "Writes the complete curated news digest to selections.json. "
            "Call this after reading ALL article files and making editorial selections. "
            "The selections object must include must_know (3+ stories), should_know (5+ stories), "
            "signals grouped by region, and a preheader sentence for inbox preview. "
            "Do NOT call until you have processed every article file. "
            "Schema validation will reject incomplete or malformed input - fix errors and retry."
        ),
        "inputSchema": SELECTIONS_SCHEMA,
    }
]

DATA_DIR = Path("/app/data/claude_input")


def validate_selections(arguments: dict) -> list[str]:
    """Validate arguments against SELECTIONS_SCHEMA. Returns list of error messages."""
    validator = Draft7Validator(SELECTIONS_SCHEMA)
    errors = []
    for error in sorted(validator.iter_errors(arguments), key=lambda e: e.path):
        path = ".".join(str(p) for p in error.path) or "(root)"
        # Provide clear, actionable error messages
        if error.validator == "type":
            errors.append(f"{path}: expected {error.validator_value}, got {type(error.instance).__name__}")
        elif error.validator == "additionalProperties":
            # Extract the unexpected field name from the error
            errors.append(f"{path}: {error.message}")
        else:
            errors.append(f"{path}: {error.message}")
    return errors


def handle_tool_call(name: str, arguments: dict) -> dict:
    """Handle tool invocation."""
    log(f"Tool call: {name}")
    if name == "write_selections":
        # Validate against schema - reject invalid input, Claude will retry
        validation_errors = validate_selections(arguments)
        if validation_errors:
            error_msg = "Schema validation failed. Fix these errors and retry:\n" + "\n".join(
                f"  - {e}" for e in validation_errors[:10]
            )
            if len(validation_errors) > 10:
                error_msg += f"\n  ... and {len(validation_errors) - 10} more errors"
            log(f"Validation failed: {len(validation_errors)} errors")
            return {"error": error_msg}

        output_path = DATA_DIR / "selections.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(arguments, f, indent=2)

        # Count items for confirmation
        must_know = len(arguments.get("must_know", []))
        should_know = len(arguments.get("should_know", []))
        signals = arguments.get("signals", {})
        signal_count = sum(len(v) for v in signals.values() if isinstance(v, list))

        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Wrote selections.json: {must_know} must_know, {should_know} should_know, {signal_count} signals",
                }
            ]
        }
    else:
        return {"error": f"Unknown tool: {name}"}


def main():
    """Main MCP server loop."""
    log("Server started")
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log(f"Invalid JSON: {line[:100]}")
            continue

        method = msg.get("method")
        id = msg.get("id")
        params = msg.get("params", {})
        log(f"Received: {method}")

        if method == "initialize":
            send_response(
                id,
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "news-digest", "version": "1.0.0"},
                },
            )

        elif method == "notifications/initialized":
            pass  # Client acknowledged init

        elif method == "tools/list":
            send_response(id, {"tools": TOOLS})

        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            result = handle_tool_call(tool_name, arguments)
            if "error" in result:
                # Tool execution errors use isError per MCP spec (not JSON-RPC error codes)
                send_response(id, {"content": [{"type": "text", "text": result["error"]}], "isError": True})
            else:
                send_response(id, result)

        elif id is not None:
            # Unknown method with id - respond with error
            send_response(id, error={"code": -32601, "message": f"Method not found: {method}"})


if __name__ == "__main__":
    main()
