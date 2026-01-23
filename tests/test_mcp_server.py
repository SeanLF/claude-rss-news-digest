"""Tests for mcp_server.py JSON-RPC protocol."""

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server import handle_tool_call, send_notification, send_response


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
            selections = {
                "must_know": [{"headline": "Test", "summary": "Sum", "why_it_matters": "Why", "sources": []}],
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
            result = handle_tool_call("write_selections", selections)

        assert "content" in result
        assert "1 must_know" in result["content"][0]["text"]

        # Verify file was created
        output_file = tmp_path / "selections.json"
        assert output_file.exists()
        with open(output_file) as f:
            saved = json.load(f)
        assert saved["must_know"][0]["headline"] == "Test"

    def test_write_selections_counts_signals(self, tmp_path):
        with patch("mcp_server.DATA_DIR", tmp_path):
            selections = {
                "must_know": [],
                "should_know": [],
                "signals": {
                    "americas": [{"headline": "A"}, {"headline": "B"}],
                    "europe": [{"headline": "C"}],
                    "asia_pacific": [],
                    "middle_east_africa": [],
                    "tech": [{"headline": "D"}],
                },
                "regional_summary": {
                    "americas": "",
                    "europe": "",
                    "asia_pacific": "",
                    "middle_east_africa": "",
                    "tech": "",
                },
            }
            result = handle_tool_call("write_selections", selections)

        assert "4 signals" in result["content"][0]["text"]

    def test_unknown_tool_returns_error(self):
        result = handle_tool_call("unknown_tool", {})
        assert "error" in result
        assert "Unknown tool" in result["error"]
