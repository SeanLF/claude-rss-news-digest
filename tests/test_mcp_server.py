"""Tests for mcp_server.py JSON-RPC protocol."""

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server import handle_tool_call, send_notification, send_response, validate_selections


# Shared test fixtures
def valid_selections():
    """Minimal valid selections structure."""
    return {
        "must_know": [],
        "should_know": [],
        "signals": {"americas": [], "europe": [], "asia_pacific": [], "middle_east_africa": [], "tech": []},
        "regional_summary": {
            "americas": "",
            "europe": "",
            "asia_pacific": "",
            "middle_east_africa": "",
            "tech": "",
        },
    }


def valid_article(headline="Test"):
    return {
        "headline": headline,
        "summary": "Summary",
        "why_it_matters": "Why",
        "sources": [{"name": "BBC", "url": "https://bbc.com", "bias": "center"}],
    }


def valid_signal(headline="Signal"):
    return {
        "headline": headline,
        "source": {"name": "BBC", "url": "https://bbc.com", "bias": "center"},
    }


class TestSendResponse:
    def test_success_response(self):
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            send_response(1, result={"status": "ok"})
            output = json.loads(mock_stdout.getvalue())

        assert output["jsonrpc"] == "2.0"
        assert output["id"] == 1
        assert output["result"] == {"status": "ok"}
        assert "error" not in output

    def test_error_response(self):
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            send_response(2, error={"code": -32600, "message": "Invalid Request"})
            output = json.loads(mock_stdout.getvalue())

        assert output["jsonrpc"] == "2.0"
        assert output["id"] == 2
        assert output["error"]["code"] == -32600
        assert "result" not in output

    def test_null_id(self):
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            send_response(None, result="done")
            output = json.loads(mock_stdout.getvalue())

        assert output["id"] is None


class TestSendNotification:
    def test_notification_with_params(self):
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            send_notification("log", {"level": "info", "message": "test"})
            output = json.loads(mock_stdout.getvalue())

        assert output["jsonrpc"] == "2.0"
        assert output["method"] == "log"
        assert output["params"]["level"] == "info"
        assert "id" not in output

    def test_notification_without_params(self):
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            send_notification("ping")
            output = json.loads(mock_stdout.getvalue())

        assert output["jsonrpc"] == "2.0"
        assert output["method"] == "ping"
        assert "params" not in output


class TestHandleToolCall:
    def test_write_selections_creates_file(self, tmp_path):
        with patch("mcp_server.DATA_DIR", tmp_path):
            selections = valid_selections()
            selections["must_know"] = [valid_article("Test")]
            result = handle_tool_call("write_selections", selections)

        assert "content" in result
        assert "1 must_know" in result["content"][0]["text"]

        output_file = tmp_path / "selections.json"
        assert output_file.exists()
        with open(output_file) as f:
            saved = json.load(f)
        assert saved["must_know"][0]["headline"] == "Test"

    def test_write_selections_counts_signals(self, tmp_path):
        with patch("mcp_server.DATA_DIR", tmp_path):
            selections = valid_selections()
            selections["signals"] = {
                "americas": [valid_signal("A"), valid_signal("B")],
                "europe": [valid_signal("C")],
                "asia_pacific": [],
                "middle_east_africa": [],
                "tech": [valid_signal("D")],
            }
            result = handle_tool_call("write_selections", selections)

        assert "4 signals" in result["content"][0]["text"]

    def test_unknown_tool_returns_error(self):
        result = handle_tool_call("unknown_tool", {})
        assert "error" in result
        assert "Unknown tool" in result["error"]


class TestValidateSelections:
    """Schema validation tests - ensure Claude gets clear errors for malformed input."""

    def test_valid_selections_passes(self):
        errors = validate_selections(valid_selections())
        assert errors == []

    def test_rejects_json_string_instead_of_array(self):
        """Regression 2026-01-26: Claude returned must_know as JSON string."""
        selections = valid_selections()
        selections["must_know"] = '[{"headline": "Test"}]'  # String, not array

        errors = validate_selections(selections)

        assert len(errors) > 0
        assert any("must_know" in e and "array" in e and "str" in e for e in errors)

    def test_rejects_missing_required_fields(self):
        selections = {"must_know": [], "should_know": []}  # Missing signals, regional_summary

        errors = validate_selections(selections)

        assert len(errors) >= 2
        assert any("signals" in e for e in errors)
        assert any("regional_summary" in e for e in errors)

    def test_rejects_wrong_signal_structure(self):
        selections = valid_selections()
        selections["signals"]["americas"] = "just a string"  # Should be array

        errors = validate_selections(selections)

        assert len(errors) > 0
        assert any("americas" in e for e in errors)

    def test_handle_tool_call_rejects_invalid_input(self, tmp_path):
        """Integration: invalid input returns error, doesn't write file."""
        with patch("mcp_server.DATA_DIR", tmp_path):
            selections = valid_selections()
            selections["must_know"] = "not an array"

            result = handle_tool_call("write_selections", selections)

        assert "error" in result
        assert "Schema validation failed" in result["error"]
        assert "must_know" in result["error"]

        # File should NOT be created
        output_file = tmp_path / "selections.json"
        assert not output_file.exists()
