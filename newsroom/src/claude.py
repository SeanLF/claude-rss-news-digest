"""Claude CLI execution for the news digest pipeline."""

import logging
import subprocess

logger = logging.getLogger(__name__)


def run_claude_command(command: str, description: str, mcp_config: str | None = None):
    """Run a Claude command with streaming output.

    Args:
        command: The slash command to run (e.g., "/news-digest-select")
        description: Human-readable description for logging
        mcp_config: Optional path to MCP config file

    Raises:
        RuntimeError: If Claude exits with non-zero code
    """
    logger.info("%s...", description)

    cmd = ["claude", "--print", "--permission-mode", "acceptEdits", command]
    if mcp_config:
        cmd.extend(["--mcp-config", mcp_config, "--allowedTools", "mcp__news-digest__write_selections"])

    logger.info("Running: %s", " ".join(cmd))

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdout is not None, "stdout=PIPE guarantees this"  # nosec B101
        for line in process.stdout:
            print(line, end="", flush=True)
        process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

    if process.returncode != 0:
        raise RuntimeError(f"Claude failed with code {process.returncode}")


def generate_selections():
    """Run Claude to select and curate stories."""
    run_claude_command("/news-digest-select", "Selecting stories", mcp_config=".mcp.json")


def generate_weekly_recap(title_lines: str) -> str:
    """Call Haiku to summarise recent RSS titles into a weekly recap.

    Returns 2-3 sentence thematic summary.
    """
    prompt = (
        "Below are RSS article titles from the past week. "
        "Summarise the major themes in 2-3 sentences. "
        "Note any multi-day themes that developed over the week. "
        "Do NOT reproduce specific headlines or titles. "
        "Write in paragraph format only.\n\n"
        f"{title_lines}"
    )

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", "haiku", "--max-turns", "1"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("Haiku recap timed out after 120s") from e

    if result.returncode != 0:
        raise RuntimeError(f"Haiku recap failed with code {result.returncode}: {result.stderr[:200]}")

    return result.stdout.strip()


def health_check() -> int:
    """Verify Claude auth is working. Returns 0 on success, 1 on failure."""
    logger.info("Running Claude auth health check...")

    result = subprocess.run(
        ["claude", "-p", "respond with 'ok'", "--max-turns", "1"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode == 0 and "ok" in result.stdout.lower():
        logger.info("Health check passed: Claude auth working")
        return 0
    else:
        logger.error("Health check FAILED: returncode=%d", result.returncode)
        if result.stderr:
            logger.error("stderr: %s", result.stderr[:500])
        return 1
