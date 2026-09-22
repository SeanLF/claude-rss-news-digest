"""Prompt text for the model stages whose prompts used to live in Python constants.

Leaf module: no internal imports (cohesion, threads, thread_synthesis and cluster_extractjoin
import this, and orchestrate imports them). Reads at call time from the same cwd-relative
directory orchestrate uses, so a run's prompts are exactly the files the image carries.
"""

from __future__ import annotations

import os
from pathlib import Path

AGENTS_DIR = Path(os.environ.get("AGENTS_DIR", ".claude/agents"))


def load_prompt_text(name: str) -> str:
    text = (AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"{name}.md has no frontmatter")
    return parts[2].strip()
