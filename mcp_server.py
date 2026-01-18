#!/usr/bin/env python3
"""MCP server for news digest - provides structured tool for selections output."""

import json
import sys
from pathlib import Path
from typing import Any

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

# Schema definitions
SOURCE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Source name (e.g., 'Wall Street Journal')"},
        "url": {"type": "string", "description": "Article URL"},
        "bias": {"type": "string", "enum": ["left", "center-left", "center", "center-right", "right"]}
    },
    "required": ["name", "url", "bias"]
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
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "angle": {"type": "string"},
                    "bias": {"type": "string"}
                },
                "required": ["source", "angle", "bias"]
            },
            "description": "Optional - only for stories with divergent framing"
        }
    },
    "required": ["headline", "summary", "why_it_matters", "sources"]
}

SIGNAL_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "Brief headline with key fact"},
        "source": SOURCE_SCHEMA
    },
    "required": ["headline", "source"]
}

SELECTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "must_know": {
            "type": "array",
            "items": ARTICLE_SCHEMA,
            "minItems": 3,
            "description": "3+ major stories you'd be embarrassed not to know"
        },
        "should_know": {
            "type": "array",
            "items": ARTICLE_SCHEMA,
            "minItems": 5,
            "description": "5+ important but not urgent stories"
        },
        "signals": {
            "type": "object",
            "properties": {
                "americas": {"type": "array", "items": SIGNAL_SCHEMA},
                "europe": {"type": "array", "items": SIGNAL_SCHEMA},
                "asia_pacific": {"type": "array", "items": SIGNAL_SCHEMA},
                "middle_east_africa": {"type": "array", "items": SIGNAL_SCHEMA},
                "tech": {"type": "array", "items": SIGNAL_SCHEMA}
            },
            "required": ["americas", "europe", "asia_pacific", "middle_east_africa", "tech"],
            "description": "One-liner signals clustered by region"
        },
        "regional_summary": {
            "type": "object",
            "properties": {
                "americas": {"type": "string"},
                "europe": {"type": "string"},
                "asia_pacific": {"type": "string"},
                "middle_east_africa": {"type": "string"}
            },
            "required": ["americas", "europe", "asia_pacific", "middle_east_africa"],
            "description": "Narrative summaries with inline markdown links"
        }
    },
    "required": ["must_know", "should_know", "signals", "regional_summary"]
}

TOOLS = [
    {
        "name": "write_selections",
        "description": "Write the curated news selections to selections.json. Call this tool with the complete selections object.",
        "inputSchema": SELECTIONS_SCHEMA
    }
]

DATA_DIR = Path("data/claude_input")

def handle_tool_call(name: str, arguments: dict) -> dict:
    """Handle tool invocation."""
    if name == "write_selections":
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
                    "text": f"Wrote selections.json: {must_know} must_know, {should_know} should_know, {signal_count} signals"
                }
            ]
        }
    else:
        return {"error": f"Unknown tool: {name}"}

def main():
    """Main MCP server loop."""
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method")
        id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            send_response(id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "news-digest", "version": "1.0.0"}
            })

        elif method == "notifications/initialized":
            pass  # Client acknowledged init

        elif method == "tools/list":
            send_response(id, {"tools": TOOLS})

        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            result = handle_tool_call(tool_name, arguments)
            if "error" in result:
                send_response(id, error={"code": -32000, "message": result["error"]})
            else:
                send_response(id, result)

        elif id is not None:
            # Unknown method with id - respond with error
            send_response(id, error={"code": -32601, "message": f"Method not found: {method}"})

if __name__ == "__main__":
    main()
