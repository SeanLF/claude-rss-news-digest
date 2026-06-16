"""Tests for utils.setup_logging -- focus on read-only-filesystem resilience.

The dead-man's switch mounts the data volume ``:ro`` and runs ``--verify-today``;
logging setup must degrade to stdout instead of crashing (a crash means no
verification and -- worse -- the alert never reports the real reason).
"""

import logging
from unittest.mock import patch

import pytest
import utils


@pytest.fixture(autouse=True)
def _restore_root_handlers():
    """Isolate root-logger mutation: setup_logging adds global handlers."""
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers.clear()
    yield
    root.handlers.clear()
    root.handlers.extend(saved)


def test_falls_back_to_stdout_when_data_dir_read_only(tmp_path):
    # Simulate the deadman :ro mount: the rotating file handler can't be opened.
    with (
        patch.object(utils, "DATA_DIR", tmp_path),
        patch.object(utils, "RotatingFileHandler", side_effect=OSError(30, "Read-only file system")),
    ):
        utils.setup_logging()  # must not raise

    handler_types = [type(h).__name__ for h in logging.getLogger().handlers]
    assert "StreamHandler" in handler_types
    assert "RotatingFileHandler" not in handler_types


def test_adds_file_handler_when_writable(tmp_path):
    with patch.object(utils, "DATA_DIR", tmp_path), patch.object(utils, "LOG_FILE", tmp_path / "digest.log"):
        utils.setup_logging()

    handler_types = [type(h).__name__ for h in logging.getLogger().handlers]
    assert "StreamHandler" in handler_types
    assert "RotatingFileHandler" in handler_types
