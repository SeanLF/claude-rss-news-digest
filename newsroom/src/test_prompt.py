#!/usr/bin/env python3
"""
Test harness for prompt experiments.

Scientific experimentation platform for news digest curation.
Compare prompt versions, models, context variations on identical inputs.
"""

import argparse
import csv
import json
import math
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# =============================================================================
# Configuration
# =============================================================================

ROOT_DIR = Path("/app")
DATA_DIR = ROOT_DIR / "data"
PROMPTS_DIR = ROOT_DIR / "newsroom" / "prompts"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
RUNS_DIR = DATA_DIR / "runs"
CLAUDE_INPUT_DIR = DATA_DIR / "claude_input"
DB_PATH = DATA_DIR / "digest.db"

# Regions for analysis (from run.py)
REGION_ORDER = ["americas", "europe", "asia_pacific", "middle_east_africa", "tech"]

# Stopwords for TF-IDF (copied from run.py for self-containment)
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "as",
        "is",
        "was",
        "are",
        "were",
        "been",
        "be",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "need",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "our",
        "their",
        "what",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "also",
        "now",
        "s",
        "t",
    ]
)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class Run:
    """A single Claude invocation with full metadata."""

    id: str  # e.g., "2026-02-02_baseline_sonnet"
    snapshot: str  # Input date
    prompt: str  # Prompt version name
    model: str  # claude model (sonnet, opus, haiku)
    context: list[str]  # Extra context flags
    created_at: datetime
    selections: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    tokens: int = 0
    duration_seconds: float = 0.0
    failed: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "snapshot": self.snapshot,
            "prompt": self.prompt,
            "model": self.model,
            "context": self.context,
            "created_at": self.created_at.isoformat(),
            "tokens": self.tokens,
            "duration_seconds": self.duration_seconds,
            "failed": self.failed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Run:
        return cls(
            id=data["id"],
            snapshot=data["snapshot"],
            prompt=data["prompt"],
            model=data["model"],
            context=data.get("context", []),
            created_at=datetime.fromisoformat(data["created_at"]),
            tokens=data.get("tokens", 0),
            duration_seconds=data.get("duration_seconds", 0.0),
            failed=data.get("failed", False),
        )


@dataclass
class Comparison:
    """Comparison results between two runs."""

    run_a: Run
    run_b: Run
    selection: dict
    tiers: dict
    tier_changes: list[dict]
    sources: dict
    regions: dict
    quality: dict


# =============================================================================
# TF-IDF Matcher (from run.py)
# =============================================================================


def tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into words, remove stopwords."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return [w for w in text.split() if w not in STOPWORDS]


class TfidfMatcher:
    """TF-IDF similarity matcher for headline comparison."""

    def __init__(self, headlines: list[str]):
        self.headlines = headlines
        self._documents = [tokenize(h) for h in headlines]
        self.idf = self._compute_idf()
        self._doc_vectors = [self._tfidf_vector(doc) for doc in self._documents]

    def _compute_idf(self) -> dict[str, float]:
        n_docs = len(self._documents)
        if n_docs == 0:
            return {}
        doc_freq: Counter[str] = Counter()
        for doc in self._documents:
            doc_freq.update(set(doc))
        return {word: math.log(n_docs / (1 + df)) for word, df in doc_freq.items()}

    def _tfidf_vector(self, doc: list[str]) -> dict[str, float]:
        if not doc:
            return {}
        tf = Counter(doc)
        max_tf = max(tf.values())
        return {word: (count / max_tf) * self.idf[word] for word, count in tf.items() if word in self.idf}

    def _cosine_similarity(self, vec1: dict[str, float], vec2: dict[str, float]) -> float:
        if not vec1 or not vec2:
            return 0.0
        shared_keys = vec1.keys() & vec2.keys()
        if not shared_keys:
            return 0.0
        dot = sum(vec1[w] * vec2[w] for w in shared_keys)
        mag1 = math.sqrt(sum(v * v for v in vec1.values()))
        mag2 = math.sqrt(sum(v * v for v in vec2.values()))
        return dot / (mag1 * mag2)

    def find_most_similar(self, text: str) -> tuple[str | None, float]:
        if not self.headlines:
            return None, 0.0
        query_vec = self._tfidf_vector(tokenize(text))
        best_headline = None
        best_score = 0.0
        for i, doc_vec in enumerate(self._doc_vectors):
            score = self._cosine_similarity(query_vec, doc_vec)
            if score > best_score:
                best_score = score
                best_headline = self.headlines[i]
        return best_headline, best_score


# =============================================================================
# Snapshot Management
# =============================================================================


def create_snapshot() -> str:
    """Copy current claude_input/ to data/snapshots/YYYY-MM-DD/"""
    if not CLAUDE_INPUT_DIR.exists():
        print("Error: data/claude_input/ not found. Run the digest pipeline first.", file=sys.stderr)
        sys.exit(1)

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    snapshot_dir = SNAPSHOTS_DIR / date_str

    # Remove existing snapshot for today if it exists
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)

    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Copy all files from claude_input (but not selections.json - that's output)
    for src_file in CLAUDE_INPUT_DIR.iterdir():
        if src_file.name != "selections.json":
            shutil.copy2(src_file, snapshot_dir / src_file.name)

    file_count = len(list(snapshot_dir.iterdir()))
    print(f"Created snapshot: {date_str} ({file_count} files)")
    return date_str


def list_snapshots() -> list[str]:
    """List all available snapshots."""
    if not SNAPSHOTS_DIR.exists():
        return []
    snapshots = sorted([d.name for d in SNAPSHOTS_DIR.iterdir() if d.is_dir()], reverse=True)
    return snapshots


def get_latest_snapshot() -> str | None:
    """Get the most recent snapshot date."""
    snapshots = list_snapshots()
    return snapshots[0] if snapshots else None


# =============================================================================
# Context Management
# =============================================================================


def get_recent_headlines(days: int = 2) -> list[dict]:
    """Get headlines from the last N days for context."""
    if not DB_PATH.exists():
        return []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                """
                SELECT headline, tier, date(shown_at) as date
                FROM shown_narratives
                WHERE shown_at > datetime('now', ?)
                ORDER BY shown_at DESC
            """,
                (f"-{days} days",),
            )
            return [{"headline": row[0], "tier": row[1], "date": row[2]} for row in cursor.fetchall()]
    except sqlite3.Error as e:
        print(f"Warning: Could not get recent headlines: {e}", file=sys.stderr)
        return []


def prepare_context(context_flags: list[str], snapshot_dir: Path):
    """Add context files based on flags."""
    if "headlines" in context_flags:
        headlines = get_recent_headlines(days=2)
        if headlines:
            csv_path = snapshot_dir / "recent_headlines.csv"
            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["headline", "date"])
                for h in headlines:
                    writer.writerow([h["headline"], h["date"]])
            print(f"  Added recent_headlines.csv ({len(headlines)} headlines)")


# =============================================================================
# Run Management
# =============================================================================


def generate_run_id(snapshot: str, prompt: str, model: str) -> str:
    """Generate a unique run ID with timestamp."""
    ts = datetime.now(UTC).strftime("%H%M%S")
    return f"{snapshot}_{prompt}_{model}_{ts}"


def limit_articles_in_csv(csv_path: Path, limit: int) -> int:
    """Truncate articles CSV to limit rows. Returns actual count."""
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    if len(rows) <= limit:
        return len(rows)

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows[:limit])

    return limit


def resolve_prompt_file(prompt: str) -> Path:
    """Resolve prompt name to file path."""
    if prompt == "baseline":
        return ROOT_DIR / ".claude" / "commands" / "news-digest-select.md"
    return PROMPTS_DIR / f"{prompt}.md"


def resolve_snapshot(snapshot: str | None) -> tuple[str, Path]:
    """Resolve snapshot name to (name, path), or exit on error."""
    if snapshot is None:
        snapshot = get_latest_snapshot()
        if snapshot is None:
            print("Error: No snapshots available. Run 'bin/test-prompt snapshot' first.", file=sys.stderr)
            sys.exit(1)

    snapshot_dir = SNAPSHOTS_DIR / snapshot
    if not snapshot_dir.exists():
        print(f"Error: Snapshot not found: {snapshot}", file=sys.stderr)
        sys.exit(1)

    return snapshot, snapshot_dir


def run_prompt(
    prompt: str,
    model: str = "sonnet",
    snapshot: str | None = None,
    context: list[str] | None = None,
    limit: int | None = None,
) -> Run:
    """Execute a run and save results."""
    context = context or []

    prompt_file = resolve_prompt_file(prompt)
    if not prompt_file.exists():
        print(f"Error: Prompt not found: {prompt_file}", file=sys.stderr)
        sys.exit(1)

    snapshot, snapshot_dir = resolve_snapshot(snapshot)

    run_id = generate_run_id(snapshot, prompt, model)
    run_dir = RUNS_DIR / run_id

    print(f"Starting run: {run_id}")
    print(f"  Snapshot: {snapshot}")
    print(f"  Prompt: {prompt}")
    print(f"  Model: {model}")
    if limit:
        print(f"  Limit: {limit} articles")
    if context:
        print(f"  Context: {', '.join(context)}")

    # Create run directory
    run_dir.mkdir(parents=True, exist_ok=True)

    # Copy snapshot to claude_input
    if CLAUDE_INPUT_DIR.exists():
        shutil.rmtree(CLAUDE_INPUT_DIR)
    CLAUDE_INPUT_DIR.mkdir(parents=True)
    for src_file in snapshot_dir.iterdir():
        shutil.copy2(src_file, CLAUDE_INPUT_DIR / src_file.name)

    # Apply article limit if requested
    if limit:
        total_limited = 0
        for csv_file in sorted(CLAUDE_INPUT_DIR.glob("articles_*.csv")):
            remaining = limit - total_limited
            if remaining <= 0:
                csv_file.unlink()  # Remove excess files
            else:
                count = limit_articles_in_csv(csv_file, remaining)
                total_limited += count
        print(f"  Limited to {total_limited} articles")

    # Add context if requested
    prepare_context(context, CLAUDE_INPUT_DIR)

    # Determine slash command to use
    if prompt == "baseline":
        slash_cmd = "/news-digest-select"
    else:
        # For experiments, copy prompt to temp command location
        test_cmd_dir = Path.home() / ".claude" / "commands"
        test_cmd_dir.mkdir(parents=True, exist_ok=True)
        test_cmd_path = test_cmd_dir / "_test-select.md"
        shutil.copy2(prompt_file, test_cmd_path)
        slash_cmd = "/_test-select"

    # Run Claude (streaming like production)
    print("  Running Claude...")
    cmd = ["claude", "--print", slash_cmd, "--permission-mode", "acceptEdits"]
    if model != "sonnet":  # Only add --model if not default
        cmd.extend(["--model", model])
    cmd.extend(["--mcp-config", "newsroom/.mcp.json", "--allowedTools", "mcp__news-digest__write_selections"])
    print(f"  Command: {' '.join(cmd)}")

    start_time = time.monotonic()
    output_lines: list[str] = []
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdout is not None
        for line in process.stdout:
            output_lines.append(line)
            print(f"    {line}", end="", flush=True)
        process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        # Clean up temp command if created
        if prompt != "baseline":
            test_cmd_path = Path.home() / ".claude" / "commands" / "_test-select.md"
            if test_cmd_path.exists():
                test_cmd_path.unlink()
    duration_seconds = time.monotonic() - start_time

    # Check for failure
    selections_path = CLAUDE_INPUT_DIR / "selections.json"
    failed = process.returncode != 0 or not selections_path.exists()

    if failed:
        # Save failed run for analysis
        with open(run_dir / "output.txt", "w") as f:
            f.writelines(output_lines)
        with open(run_dir / "run.json", "w") as f:
            json.dump(
                {
                    "id": run_id,
                    "snapshot": snapshot,
                    "prompt": prompt,
                    "model": model,
                    "context": context,
                    "created_at": datetime.now(UTC).isoformat(),
                    "failed": True,
                    "duration_seconds": round(duration_seconds, 1),
                },
                f,
                indent=2,
            )
        print("Error: Claude did not produce selections.json", file=sys.stderr)
        print(f"  Output saved to: {run_dir / 'output.txt'}", file=sys.stderr)
        sys.exit(1)

    with open(selections_path) as f:
        selections = json.load(f)

    run = Run(
        id=run_id,
        snapshot=snapshot,
        prompt=prompt,
        model=model,
        context=context,
        created_at=datetime.now(UTC),
        selections=selections,
        duration_seconds=round(duration_seconds, 1),
    )
    run.metrics = compute_metrics(selections)

    # Save run data
    for filename, data in [("run.json", run.to_dict()), ("selections.json", selections), ("metrics.json", run.metrics)]:
        with open(run_dir / filename, "w") as f:
            json.dump(data, f, indent=2)

    print(f"  Saved to: {run_dir}")
    print(f"  Stories: {run.metrics.get('total_stories', 0)}")

    return run


def load_run(run_id: str) -> Run:
    """Load a run by ID or prompt name (returns latest run for that prompt)."""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        matching = find_runs_by_prompt(run_id)
        if not matching:
            raise ValueError(f"Run not found: {run_id}")
        run_dir = RUNS_DIR / matching[0]

    with open(run_dir / "run.json") as f:
        run = Run.from_dict(json.load(f))

    # Failed runs don't have selections/metrics files
    if not run.failed:
        with open(run_dir / "selections.json") as f:
            run.selections = json.load(f)
        with open(run_dir / "metrics.json") as f:
            run.metrics = json.load(f)

    return run


def find_runs_by_prompt(prompt: str) -> list[str]:
    """Find run IDs containing a prompt name, sorted by date desc."""
    if not RUNS_DIR.exists():
        return []
    matching = [d.name for d in RUNS_DIR.iterdir() if d.is_dir() and f"_{prompt}_" in d.name]
    return sorted(matching, reverse=True)


def list_runs(snapshot: str | None = None) -> list[Run]:
    """List all runs, optionally filtered by snapshot."""
    if not RUNS_DIR.exists():
        return []

    runs = []
    for run_dir in sorted(RUNS_DIR.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        try:
            with open(run_dir / "run.json") as f:
                run_data = json.load(f)
            run = Run.from_dict(run_data)
            # Load metrics for display
            metrics_path = run_dir / "metrics.json"
            if metrics_path.exists():
                with open(metrics_path) as f:
                    run.metrics = json.load(f)
            if snapshot is None or run.snapshot == snapshot:
                runs.append(run)
        except json.JSONDecodeError, KeyError, FileNotFoundError:
            continue

    return runs


# =============================================================================
# Metrics Computation
# =============================================================================


def compute_metrics(selections: dict) -> dict:
    """Compute metrics from selections."""
    must_know = selections.get("must_know", [])
    should_know = selections.get("should_know", [])
    signals = selections.get("signals", {})
    tiered_articles = must_know + should_know

    # Story counts
    signals_count = sum(len(signals.get(r, [])) for r in REGION_ORDER)
    metrics = {
        "must_know_count": len(must_know),
        "should_know_count": len(should_know),
        "signals_count": signals_count,
        "total_stories": len(must_know) + len(should_know) + signals_count,
    }

    # Source diversity - collect from tiered articles and signals in one pass
    all_sources: set[str] = set()
    bias_counts: Counter[str] = Counter()

    def collect_source(src: dict) -> None:
        if name := src.get("name"):
            all_sources.add(name)
        if bias := src.get("bias"):
            bias_counts[bias] += 1

    for article in tiered_articles:
        for src in article.get("sources", []):
            collect_source(src)

    for region in REGION_ORDER:
        for item in signals.get(region, []):
            collect_source(item.get("source", {}))

    metrics["unique_sources"] = len(all_sources)
    metrics["bias_distribution"] = dict(bias_counts)
    metrics["region_distribution"] = {r: len(signals.get(r, [])) for r in REGION_ORDER}

    # Quality metrics from tiered articles
    if tiered_articles:
        headline_lengths = [len(a.get("headline", "")) for a in tiered_articles]
        summary_lengths = [len(a.get("summary", "").split()) for a in tiered_articles]
        why_present = sum(1 for a in tiered_articles if a.get("why_it_matters"))

        metrics["avg_headline_length"] = sum(headline_lengths) // len(headline_lengths)
        metrics["avg_summary_words"] = sum(summary_lengths) // len(summary_lengths)
        metrics["why_it_matters_pct"] = round(100 * why_present / len(tiered_articles))

    return metrics


def extract_headlines(selections: dict) -> list[str]:
    """Extract all headlines from selections."""
    headlines = [a.get("headline", "") for a in selections.get("must_know", []) + selections.get("should_know", [])]
    for region in REGION_ORDER:
        headlines.extend(item.get("headline", "") for item in selections.get("signals", {}).get(region, []))
    return [h for h in headlines if h]


def get_headline_tier(headline: str, selections: dict) -> str:
    """Get the tier of a headline."""
    for article in selections.get("must_know", []):
        if article.get("headline") == headline:
            return "must_know"
    for article in selections.get("should_know", []):
        if article.get("headline") == headline:
            return "should_know"
    return "signal"


# =============================================================================
# Comparison
# =============================================================================


def compare_runs(run_a: Run, run_b: Run) -> Comparison:
    """Compare two runs."""
    sel_a = run_a.selections
    sel_b = run_b.selections

    # Extract headlines
    headlines_a = extract_headlines(sel_a)
    headlines_b = extract_headlines(sel_b)

    # Find matches using TF-IDF
    matcher_b = TfidfMatcher(headlines_b)
    matched = []
    added = []
    dropped = []

    for ha in headlines_a:
        best_match, score = matcher_b.find_most_similar(ha)
        if score >= 0.5:  # Threshold for considering a match
            matched.append({"a": ha, "b": best_match, "score": score})
        else:
            dropped.append(ha)

    # Find added stories (in B but not matched from A)
    matched_b_headlines = {m["b"] for m in matched}
    for hb in headlines_b:
        if hb not in matched_b_headlines:
            added.append(hb)

    selection = {
        "stories_a": len(headlines_a),
        "stories_b": len(headlines_b),
        "matched": len(matched),
        "overlap_pct": round(100 * len(matched) / max(len(headlines_a), 1)),
        "added": added,
        "dropped": dropped,
    }

    # Helper to extract tier counts from a run's metrics
    def tier_counts(run: Run) -> dict:
        return {
            "must_know": run.metrics.get("must_know_count", 0),
            "should_know": run.metrics.get("should_know_count", 0),
            "signals": run.metrics.get("signals_count", 0),
        }

    tiers = {"a": tier_counts(run_a), "b": tier_counts(run_b)}

    # Tier changes for matched stories
    tier_changes = [
        {"headline": m["a"], "from": get_headline_tier(m["a"], sel_a), "to": get_headline_tier(m["b"], sel_b)}
        for m in matched
        if get_headline_tier(m["a"], sel_a) != get_headline_tier(m["b"], sel_b)
    ]

    # Sources comparison
    sources = {
        "unique_a": run_a.metrics.get("unique_sources", 0),
        "unique_b": run_b.metrics.get("unique_sources", 0),
        "bias_a": run_a.metrics.get("bias_distribution", {}),
        "bias_b": run_b.metrics.get("bias_distribution", {}),
    }

    # Regions comparison
    regions = {"a": run_a.metrics.get("region_distribution", {}), "b": run_b.metrics.get("region_distribution", {})}

    # Quality comparison
    quality = {
        "headline_length_a": run_a.metrics.get("avg_headline_length", 0),
        "headline_length_b": run_b.metrics.get("avg_headline_length", 0),
        "summary_words_a": run_a.metrics.get("avg_summary_words", 0),
        "summary_words_b": run_b.metrics.get("avg_summary_words", 0),
        "why_pct_a": run_a.metrics.get("why_it_matters_pct", 0),
        "why_pct_b": run_b.metrics.get("why_it_matters_pct", 0),
    }

    return Comparison(
        run_a=run_a,
        run_b=run_b,
        selection=selection,
        tiers=tiers,
        tier_changes=tier_changes,
        sources=sources,
        regions=regions,
        quality=quality,
    )


def format_bias(bias_dict: dict) -> str:
    """Format bias distribution as compact string (e.g., 'L:2 C:5 R:3')."""
    abbrev = {
        "far-left": "FL",
        "left": "L",
        "lean-left": "LL",
        "center": "C",
        "lean-right": "LR",
        "right": "R",
        "far-right": "FR",
    }
    order = ["far-left", "left", "lean-left", "center", "lean-right", "right", "far-right"]
    parts = [f"{abbrev[b]}:{bias_dict[b]}" for b in order if bias_dict.get(b)]
    return " ".join(parts)


def format_diff(val_a: int, val_b: int) -> str:
    """Format a value comparison with optional diff indicator."""
    diff = val_b - val_a
    return f"({diff:+d})" if diff != 0 else ""


def format_headline_list(headlines: list[str], prefix: str, max_show: int = 5) -> list[str]:
    """Format a list of headlines with truncation."""
    lines = []
    for h in headlines[:max_show]:
        display = f"{h[:60]}..." if len(h) > 60 else h
        lines.append(f'    {prefix} "{display}"')
    if len(headlines) > max_show:
        lines.append(f"    ... and {len(headlines) - max_show} more")
    return lines


def format_comparison(comp: Comparison, output_format: str = "text") -> str:
    """Format comparison results."""
    if output_format == "json":
        return json.dumps(
            {
                "run_a": comp.run_a.id,
                "run_b": comp.run_b.id,
                "selection": comp.selection,
                "tiers": comp.tiers,
                "tier_changes": comp.tier_changes,
                "sources": comp.sources,
                "regions": comp.regions,
                "quality": comp.quality,
            },
            indent=2,
        )

    # Human-readable format
    sel = comp.selection
    src = comp.sources
    q = comp.quality

    lines = [
        f"Comparison: {comp.run_a.prompt} vs {comp.run_b.prompt} ({comp.run_a.snapshot})",
        "=" * 60,
        "",
        "SELECTION",
        f"  Stories: {sel['stories_a']} vs {sel['stories_b']} {format_diff(sel['stories_a'], sel['stories_b'])}",
        f"  Overlap: {sel['matched']} matched ({sel['overlap_pct']}%)",
    ]

    if sel["added"]:
        lines.append("  Added:")
        lines.extend(format_headline_list(sel["added"], "+"))
    if sel["dropped"]:
        lines.append("  Dropped:")
        lines.extend(format_headline_list(sel["dropped"], "-"))

    lines.extend(["", "TIER DISTRIBUTION", f"            {comp.run_a.prompt:>12}  {comp.run_b.prompt:>12}"])
    for tier in ["must_know", "should_know", "signals"]:
        a_val, b_val = comp.tiers["a"][tier], comp.tiers["b"][tier]
        lines.append(f"  {tier:12} {a_val:>10}  {b_val:>10}  {format_diff(a_val, b_val)}")

    if comp.tier_changes:
        lines.extend(["", "TIER CHANGES (matched stories)"])
        for change in comp.tier_changes[:5]:
            arrow = "+" if change["to"] == "must_know" else "-"
            lines.append(f'  {arrow} "{change["headline"][:50]}" {change["from"]} -> {change["to"]}')
        if len(comp.tier_changes) > 5:
            lines.append(f"  ... and {len(comp.tier_changes) - 5} more")

    lines.extend(
        [
            "",
            "SOURCES",
            f"  Unique: {src['unique_a']} -> {src['unique_b']}",
            f"  Bias: {format_bias(src['bias_a'])} -> {format_bias(src['bias_b'])}",
            "",
            "REGIONS",
        ]
    )

    for region in REGION_ORDER:
        a_val, b_val = comp.regions["a"].get(region, 0), comp.regions["b"].get(region, 0)
        lines.append(f"  {region}: {a_val} -> {b_val} {format_diff(a_val, b_val)}")

    lines.extend(
        [
            "",
            "QUALITY",
            f"  Avg headline length: {q['headline_length_a']} -> {q['headline_length_b']} chars",
            f"  Avg summary length: {q['summary_words_a']} -> {q['summary_words_b']} words",
            f"  why_it_matters present: {q['why_pct_a']}% -> {q['why_pct_b']}%",
        ]
    )

    return "\n".join(lines)


# =============================================================================
# CLI
# =============================================================================


def cmd_snapshot(args):
    """Create a snapshot of current input."""
    create_snapshot()


def cmd_list(args):
    """List snapshots."""
    snapshots = list_snapshots()
    if not snapshots:
        print("No snapshots found. Run the digest pipeline first, then 'bin/test-prompt snapshot'.")
        return

    print("Available snapshots:")
    for s in snapshots:
        # Count files
        snapshot_dir = SNAPSHOTS_DIR / s
        file_count = len(list(snapshot_dir.iterdir()))
        print(f"  {s} ({file_count} files)")


def cmd_run(args):
    """Execute a run."""
    context = args.context.split(",") if args.context else []
    run_prompt(
        prompt=args.prompt,
        model=args.model,
        snapshot=args.date,
        context=context,
        limit=args.limit,
    )


def cmd_diff(args):
    """Compare two runs."""
    try:
        run_a = load_run(args.run_a)
        run_b = load_run(args.run_b)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    comp = compare_runs(run_a, run_b)
    print(format_comparison(comp, args.format))


def cmd_runs(args):
    """List runs."""
    runs = list_runs(snapshot=args.snapshot)
    if not runs:
        print("No runs found.")
        return

    print("Runs:")
    for run in runs:
        context_str = f" +{','.join(run.context)}" if run.context else ""
        duration_str = f", {run.duration_seconds}s" if run.duration_seconds else ""
        if run.failed:
            print(f"  {run.id}{context_str} [FAILED]")
            print(f"    Model: {run.model}{duration_str}")
        else:
            print(f"  {run.id}{context_str}")
            print(f"    Model: {run.model}, Stories: {run.metrics.get('total_stories', '?')}{duration_str}")


def main():
    parser = argparse.ArgumentParser(
        description="Test harness for prompt experiments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # snapshot
    p_snapshot = subparsers.add_parser("snapshot", help="Save today's input as a snapshot")
    p_snapshot.set_defaults(func=cmd_snapshot)

    # list
    p_list = subparsers.add_parser("list", help="List available snapshots")
    p_list.set_defaults(func=cmd_list)

    # run
    p_run = subparsers.add_parser("run", help="Execute a run")
    p_run.add_argument("prompt", help="Prompt name (e.g., 'baseline')")
    p_run.add_argument("--model", default="sonnet", choices=["sonnet", "opus", "haiku"])
    p_run.add_argument("--date", help="Snapshot date (default: latest)")
    p_run.add_argument("--context", help="Context flags, comma-separated (e.g., 'headlines')")
    p_run.add_argument("--limit", type=int, help="Limit articles fed to Claude (for testing input size)")
    p_run.set_defaults(func=cmd_run)

    # diff
    p_diff = subparsers.add_parser("diff", help="Compare two runs")
    p_diff.add_argument("run_a", help="First run ID or prompt name")
    p_diff.add_argument("run_b", help="Second run ID or prompt name")
    p_diff.add_argument("--format", choices=["text", "json"], default="text")
    p_diff.set_defaults(func=cmd_diff)

    # runs
    p_runs = subparsers.add_parser("runs", help="List all runs")
    p_runs.add_argument("--snapshot", help="Filter by snapshot date")
    p_runs.set_defaults(func=cmd_runs)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
