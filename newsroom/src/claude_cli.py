"""Claude CLI wrapper.

Thin, reusable wrapper around `claude --print` / `--output-format stream-json`.
Drop into any project; no installation required.

Usage:
    from claude_cli import run_sync, stream_sync   # sync (batch pipelines)
    from claude_cli import run, stream             # async (web servers)
"""

import asyncio
import contextlib
import json
import logging
import os
import signal
import subprocess
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_cmd(
    prompt: str,
    *,
    model: str = "sonnet",
    system_prompt: str | None = None,
    output_format: str | None = None,
    verbose: bool = False,
    permission_mode: str | None = None,
    allowed_tools: str | None = None,
    mcp_config: str | Path | None = None,
    json_schema: str | None = None,
    max_turns: int | None = None,
) -> list[str]:
    cmd = ["claude", "--print", "--model", model]
    if system_prompt is not None:
        cmd.extend(["--system-prompt", system_prompt])
    if output_format is not None:
        cmd.extend(["--output-format", output_format])
    if verbose:
        cmd.append("--verbose")
    if permission_mode is not None:
        cmd.extend(["--permission-mode", permission_mode])
    if allowed_tools is not None:
        cmd.extend(["--allowed-tools", allowed_tools])
    if mcp_config is not None:
        cmd.extend(["--mcp-config", str(mcp_config)])
    if json_schema is not None:
        cmd.extend(["--json-schema", json_schema])
    if max_turns is not None:
        cmd.extend(["--max-turns", str(max_turns)])
    cmd.extend(["--", prompt])
    return cmd


def _kill_proc(proc: subprocess.Popen | asyncio.subprocess.Process) -> None:
    """Kill process group; fall back to direct kill if pgid lookup fails."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError, PermissionError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


# ---------------------------------------------------------------------------
# Synchronous API
# ---------------------------------------------------------------------------


def run_sync(
    prompt: str,
    *,
    model: str = "sonnet",
    system_prompt: str | None = None,
    output_format: str | None = None,
    permission_mode: str | None = None,
    allowed_tools: str | None = None,
    mcp_config: str | Path | None = None,
    json_schema: str | None = None,
    max_turns: int | None = None,
    timeout: int = 30,
    cwd: str | Path | None = None,
) -> str:
    """Run a prompt synchronously and return the full output."""
    cmd = _build_cmd(
        prompt,
        model=model,
        system_prompt=system_prompt,
        output_format=output_format,
        permission_mode=permission_mode,
        allowed_tools=allowed_tools,
        mcp_config=mcp_config,
        json_schema=json_schema,
        max_turns=max_turns,
    )
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        start_new_session=True,
        cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude failed (exit {result.returncode}): {result.stderr[:500]}")
    return result.stdout.strip()


def stream_sync(
    prompt: str,
    *,
    model: str = "sonnet",
    system_prompt: str | None = None,
    permission_mode: str | None = None,
    allowed_tools: str | None = None,
    mcp_config: str | Path | None = None,
    max_turns: int | None = None,
    cwd: str | Path | None = None,
) -> Generator[dict]:
    """Stream a prompt synchronously, yielding parsed NDJSON event dicts."""
    cmd = _build_cmd(
        prompt,
        model=model,
        system_prompt=system_prompt,
        output_format="stream-json",
        verbose=True,
        permission_mode=permission_mode,
        allowed_tools=allowed_tools,
        mcp_config=mcp_config,
        max_turns=max_turns,
    )
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
        cwd=cwd,
    )
    killed = False
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("stream_sync non-JSON: %s", raw[:120])
    finally:
        if proc.poll() is None:
            _kill_proc(proc)
            killed = True
        proc.wait()

    assert proc.stderr is not None
    stderr_out = proc.stderr.read()
    if not killed and proc.returncode != 0:
        raise RuntimeError(f"claude failed (exit {proc.returncode}): {stderr_out[:500]}")


# ---------------------------------------------------------------------------
# Async API
# ---------------------------------------------------------------------------


async def run(
    prompt: str,
    *,
    model: str = "sonnet",
    system_prompt: str | None = None,
    output_format: str | None = None,
    permission_mode: str | None = None,
    allowed_tools: str | None = None,
    mcp_config: str | Path | None = None,
    json_schema: str | None = None,
    max_turns: int | None = None,
    timeout: int = 600,
    cwd: str | Path | None = None,
) -> str:
    """Run a prompt asynchronously and return the full output."""
    cmd = _build_cmd(
        prompt,
        model=model,
        system_prompt=system_prompt,
        output_format=output_format,
        permission_mode=permission_mode,
        allowed_tools=allowed_tools,
        mcp_config=mcp_config,
        json_schema=json_schema,
        max_turns=max_turns,
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        cwd=cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        _kill_proc(proc)
        await proc.wait()
        raise
    if proc.returncode != 0:
        raise RuntimeError(f"claude failed (exit {proc.returncode}): {stderr.decode()[:500]}")
    return stdout.decode()


async def stream(
    prompt: str,
    *,
    model: str = "sonnet",
    system_prompt: str | None = None,
    permission_mode: str | None = None,
    allowed_tools: str | None = None,
    mcp_config: str | Path | None = None,
    max_turns: int | None = None,
    cwd: str | Path | None = None,
) -> AsyncGenerator[dict]:
    """Stream a prompt asynchronously, yielding parsed NDJSON event dicts."""
    cmd = _build_cmd(
        prompt,
        model=model,
        system_prompt=system_prompt,
        output_format="stream-json",
        verbose=True,
        permission_mode=permission_mode,
        allowed_tools=allowed_tools,
        mcp_config=mcp_config,
        max_turns=max_turns,
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        cwd=cwd,
    )
    killed = False
    try:
        assert proc.stdout is not None
        async for line in proc.stdout:
            text = line.decode().strip()
            if not text:
                continue
            try:
                yield json.loads(text)
            except json.JSONDecodeError:
                logger.debug("stream non-JSON: %s", text[:120])
    finally:
        if proc.returncode is None:
            _kill_proc(proc)
            killed = True
        assert proc.stderr is not None
        stderr_data = await proc.stderr.read()
        await proc.wait()
        if not killed and proc.returncode != 0:
            raise RuntimeError(f"claude failed (exit {proc.returncode}): {stderr_data.decode()[:500]}")
