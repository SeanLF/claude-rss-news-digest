"""Tests for JSONL session usage parser."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from usage import (
    _compute_cost,
    _extract_dispatches,
    _match_subagents,
    _sum_usage,
    parse_session_usage,
)


class TestExtractDispatches:
    def test_extracts_agent_calls(self):
        lines = [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Agent",
                            "input": {
                                "description": "CLUSTER articles into story groups",
                                "prompt": "You are a news clustering agent. Group articles.",
                            },
                        }
                    ],
                }
            }
        ]
        dispatches = _extract_dispatches(lines)
        assert len(dispatches) == 1
        assert dispatches[0]["label"] == "cluster"
        assert dispatches[0]["prompt_prefix"] == "You are a news clustering agent. Group articles."[:200]

    def test_label_from_first_word_lowercased(self):
        lines = [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Agent",
                            "input": {
                                "description": "WRITE headlines and summaries",
                                "prompt": "You are a news writer.",
                            },
                        }
                    ],
                }
            }
        ]
        assert _extract_dispatches(lines)[0]["label"] == "write"

    def test_skips_non_agent_tools(self):
        lines = [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"path": "/foo"}},
                    ],
                }
            }
        ]
        assert _extract_dispatches(lines) == []

    def test_multiple_dispatches_in_order(self):
        lines = [
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Agent",
                            "input": {"description": "CLUSTER articles", "prompt": "cluster prompt"},
                        },
                        {
                            "type": "tool_use",
                            "name": "Agent",
                            "input": {"description": "RECAP themes", "prompt": "recap prompt"},
                        },
                    ],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Agent",
                            "input": {"description": "SELECT stories", "prompt": "select prompt"},
                        },
                    ],
                }
            },
        ]
        dispatches = _extract_dispatches(lines)
        assert [d["label"] for d in dispatches] == ["cluster", "recap", "select"]


class TestMatchSubagents:
    def test_matches_by_prompt_prefix(self):
        cluster_prompt = "You are a news clustering agent. Group articles."
        select_prompt = "You are a news editor. Assign tiers."
        dispatches = [
            {"label": "cluster", "prompt_prefix": cluster_prompt[:200]},
            {"label": "select", "prompt_prefix": select_prompt[:200]},
        ]
        subagent_prompts = {
            "agent-aaa.jsonl": select_prompt,
            "agent-bbb.jsonl": cluster_prompt,
        }
        matched = _match_subagents(dispatches, subagent_prompts)
        assert matched["agent-bbb.jsonl"] == "cluster"
        assert matched["agent-aaa.jsonl"] == "select"

    def test_unmatched_gets_unknown(self):
        dispatches = [{"label": "cluster", "prompt_prefix": "You are a clustering agent"}]
        subagent_prompts = {"agent-aaa.jsonl": "Something completely different"}
        matched = _match_subagents(dispatches, subagent_prompts)
        assert matched["agent-aaa.jsonl"] == "unknown"

    def test_empty_dispatches(self):
        matched = _match_subagents([], {"agent-aaa.jsonl": "some prompt"})
        assert matched["agent-aaa.jsonl"] == "unknown"


class TestSumUsage:
    def _make_assistant_line(self, model, input_t=0, output_t=0, cache_w=0, cache_r=0):
        return {
            "message": {
                "role": "assistant",
                "model": model,
                "usage": {
                    "input_tokens": input_t,
                    "output_tokens": output_t,
                    "cache_creation_input_tokens": cache_w,
                    "cache_read_input_tokens": cache_r,
                },
            }
        }

    def test_single_model(self):
        lines = [
            self._make_assistant_line("claude-sonnet-4-6", input_t=100, output_t=200, cache_w=300, cache_r=400),
            self._make_assistant_line("claude-sonnet-4-6", input_t=50, output_t=60, cache_w=70, cache_r=80),
        ]
        result = _sum_usage(lines)
        assert result == {
            "claude-sonnet-4-6": {"input": 150, "output": 260, "cache_write": 370, "cache_read": 480},
        }

    def test_multiple_models(self):
        lines = [
            self._make_assistant_line("claude-sonnet-4-6", input_t=100, output_t=200),
            self._make_assistant_line("claude-opus-4-6", input_t=50, output_t=60),
        ]
        result = _sum_usage(lines)
        assert "claude-sonnet-4-6" in result
        assert "claude-opus-4-6" in result

    def test_skips_user_messages(self):
        lines = [{"message": {"role": "user", "content": "hello"}}]
        assert _sum_usage(lines) == {}

    def test_skips_missing_usage(self):
        lines = [{"message": {"role": "assistant", "model": "claude-sonnet-4-6", "content": []}}]
        assert _sum_usage(lines) == {}


class TestComputeCost:
    def test_sonnet_pricing(self):
        usage = {"input": 1_000_000, "output": 0, "cache_write": 0, "cache_read": 0}
        assert _compute_cost("claude-sonnet-4-6", usage) == 3.0

    def test_opus_pricing(self):
        usage = {"input": 1_000_000, "output": 0, "cache_write": 0, "cache_read": 0}
        assert _compute_cost("claude-opus-4-6", usage) == 15.0

    def test_haiku_pricing(self):
        usage = {"input": 1_000_000, "output": 0, "cache_write": 0, "cache_read": 0}
        assert _compute_cost("claude-haiku-4-5-20251001", usage) == 0.8

    def test_unknown_model_defaults_to_sonnet(self):
        usage = {"input": 1_000_000, "output": 0, "cache_write": 0, "cache_read": 0}
        assert _compute_cost("claude-future-model-7", usage) == 3.0

    def test_all_token_types(self):
        usage = {"input": 1_000_000, "output": 1_000_000, "cache_write": 1_000_000, "cache_read": 1_000_000}
        # sonnet: 3 + 15 + 3.75 + 0.30 = 22.05
        assert _compute_cost("claude-sonnet-4-6", usage) == 22.05


class TestParseSessionUsage:
    def test_no_projects_dir(self, tmp_path):
        """Returns empty list when projects dir doesn't exist."""
        result = parse_session_usage(tmp_path / "nonexistent")
        assert result == []

    def test_empty_projects_dir(self, tmp_path):
        """Returns empty list when no JSONL files exist."""
        projects_dir = tmp_path / "projects" / "-app"
        projects_dir.mkdir(parents=True)
        result = parse_session_usage(projects_dir)
        assert result == []

    def test_parses_dispatcher_only(self, tmp_path):
        """Parses a session with no subagents."""
        projects_dir = tmp_path / "projects" / "-app"
        projects_dir.mkdir(parents=True)

        session_file = projects_dir / "abc-123.jsonl"
        lines = [
            {
                "message": {
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 200,
                        "cache_creation_input_tokens": 300,
                        "cache_read_input_tokens": 400,
                    },
                }
            }
        ]
        session_file.write_text("\n".join(json.dumps(row) for row in lines))

        result = parse_session_usage(projects_dir)
        assert len(result) == 1
        assert result[0]["subagent"] == "dispatcher"
        assert result[0]["model"] == "claude-sonnet-4-6"
        assert result[0]["input_tokens"] == 100

    def test_parses_with_subagents(self, tmp_path):
        """Parses a session with subagent files, matching by prompt content."""
        projects_dir = tmp_path / "projects" / "-app"
        projects_dir.mkdir(parents=True)

        cluster_prompt = "You are a news clustering agent. Group articles covering the same story."

        session_id = "abc-123"
        session_file = projects_dir / f"{session_id}.jsonl"
        parent_lines = [
            {
                "message": {
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Agent",
                            "input": {
                                "description": "CLUSTER articles into story groups",
                                "prompt": cluster_prompt,
                            },
                        }
                    ],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 20,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                }
            }
        ]
        session_file.write_text("\n".join(json.dumps(row) for row in parent_lines))

        # Create subagent dir and file
        sub_dir = projects_dir / session_id / "subagents"
        sub_dir.mkdir(parents=True)
        sub_file = sub_dir / "agent-aaa111.jsonl"
        sub_lines = [
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": cluster_prompt}],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 500,
                        "output_tokens": 600,
                        "cache_creation_input_tokens": 700,
                        "cache_read_input_tokens": 800,
                    },
                }
            },
        ]
        sub_file.write_text("\n".join(json.dumps(row) for row in sub_lines))

        result = parse_session_usage(projects_dir)
        assert len(result) == 2
        roles = {r["subagent"] for r in result}
        assert roles == {"dispatcher", "cluster"}
