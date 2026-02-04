"""Claude CLI execution for the news digest pipeline."""

import subprocess


def run_claude_command(command: str, description: str, mcp_config: str | None = None, log_fn=None):
    """Run a Claude command with streaming output.

    Args:
        command: The slash command to run (e.g., "/news-digest-select")
        description: Human-readable description for logging
        mcp_config: Optional path to MCP config file
        log_fn: Optional logging function (message, level)

    Raises:
        RuntimeError: If Claude exits with non-zero code
    """
    if log_fn:
        log_fn(f"{description}...")

    cmd = ["claude", "--print", "--permission-mode", "acceptEdits", command]
    if mcp_config:
        cmd.extend(["--mcp-config", mcp_config, "--allowedTools", "mcp__news-digest__write_selections"])

    if log_fn:
        log_fn(f"Running: {' '.join(cmd)}")

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


def generate_selections(log_fn=None):
    """Pass 1: Run Claude to select and curate stories."""
    run_claude_command("/news-digest-select", "Pass 1: Selecting stories", mcp_config=".mcp.json", log_fn=log_fn)


def health_check(log_fn=None) -> int:
    """Verify Claude auth is working. Returns 0 on success, 1 on failure."""
    if log_fn:
        log_fn("Running Claude auth health check...")

    result = subprocess.run(
        ["claude", "-p", "respond with 'ok'", "--max-turns", "1"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode == 0 and "ok" in result.stdout.lower():
        if log_fn:
            log_fn("Health check passed: Claude auth working")
        return 0
    else:
        if log_fn:
            log_fn(f"Health check FAILED: returncode={result.returncode}", "ERROR")
            if result.stderr:
                log_fn(f"stderr: {result.stderr[:500]}", "ERROR")
        return 1
