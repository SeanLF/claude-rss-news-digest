"""bin/ops: read-only operator queries against prod, over the SSH channel that already exists.

The security property under test is that this tool can only READ. It runs on the box with the
data volume mounted, so a payload that could write would be a foot-gun with production data
under it. Every subcommand's payload must open the database read-only, and none may carry a
write verb.
"""

import os
import sqlite3
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

OPS_PATH = Path(__file__).resolve().parents[2] / "bin" / "ops"


def _load():
    loader = SourceFileLoader("ops", str(OPS_PATH))
    spec = spec_from_loader("ops", loader)
    mod = module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ops = _load()

SQL_SUBCOMMANDS = ("run", "usage", "health", "artifacts", "artifact")
WRITE_VERBS = ("insert ", "update ", "delete ", "drop ", "alter ", "create ", "attach ")


@pytest.mark.parametrize("sub", SQL_SUBCOMMANDS)
def test_every_sql_payload_opens_the_database_read_only(sub):
    """mode=ro is the whole safety story: SQLite itself refuses the write, so a bug in this
    script cannot damage production data."""
    payload = ops.build_payload(sub)
    assert "mode=ro" in payload
    assert "uri=True" in payload


@pytest.mark.parametrize("sub", SQL_SUBCOMMANDS)
def test_no_payload_carries_a_write_verb(sub):
    payload = ops.build_payload(sub).lower()
    for verb in WRITE_VERBS:
        assert verb not in payload, f"{sub} payload contains {verb!r}"


def _scratch_db(tmp_path):
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.executescript(
        "create table digest_runs(id integer primary key);"
        "create table run_artifacts(run_id int, artifact_name text, content text);"
        "insert into digest_runs(id) values (285);"
        "insert into run_artifacts values (285, 'clusters.json', 'PAYLOAD-OK');"
    )
    con.commit()
    con.close()
    return db


def _run_payload(payload, db, name, run="285"):
    return subprocess.run(
        [sys.executable, "-c", payload],
        env={**os.environ, "OPS_RUN": run, "OPS_NAME": name},
        capture_output=True,
        text=True,
    )


def test_a_hostile_artifact_name_cannot_reach_sql(tmp_path):
    """The real data path, executed: OPS_NAME goes in as a bound parameter at runtime.

    The earlier version of this test only inspected build_payload's text, which the review
    showed would still pass if the generated script concatenated OPS_NAME into the SQL. This
    one runs the script against a scratch database and checks the table is still standing.
    """
    db = _scratch_db(tmp_path)
    payload = ops.build_payload("artifact", db=str(db))

    hostile = _run_payload(payload, db, "'; drop table digest_runs--")
    assert hostile.returncode == 1, hostile.stderr
    assert "no such artifact" in hostile.stderr

    con = sqlite3.connect(db)
    assert con.execute("select count(*) from digest_runs").fetchone()[0] == 1
    assert con.execute("select count(*) from run_artifacts").fetchone()[0] == 1
    con.close()

    good = _run_payload(payload, db, "clusters.json")
    assert good.returncode == 0, good.stderr
    assert good.stdout == "PAYLOAD-OK"


def test_the_payload_cannot_write_even_to_a_writable_file(tmp_path):
    """mode=ro is the layer that survives if the :ro mount is ever dropped, so it is tested
    on a file the process CAN write at the filesystem level."""
    db = _scratch_db(tmp_path)
    payload = ops.build_payload("run", db=str(db)).replace(
        "rows = [dict(r) for r in conn.execute(",
        'conn.execute("delete from digest_runs")\nrows = [dict(r) for r in conn.execute(',
    )
    r = _run_payload(payload, db, "")
    assert r.returncode != 0
    assert "readonly" in r.stderr.lower()


def test_run_id_defaults_to_the_latest_run():
    payload = ops.build_payload("run")
    assert "max(id)" in payload.lower()


def test_journal_is_scoped_to_the_unit_and_bounded():
    cmd = ops.journal_command(since="1h", lines=200, grep=None)
    assert "news-digest.service" in cmd
    assert "-n 200" in cmd or "--lines 200" in cmd


def test_a_padded_relative_window_is_still_normalised():
    """`_relative_time` matched on the stripped value but substituted the original, so " 6h"
    became "- 6h" and journalctl rejected it (found in review)."""
    assert "--since -6h" in ops.journal_command(since=" 6h ", lines=10, grep=None)


def test_extra_positional_arguments_are_refused(capsys):
    with pytest.raises(SystemExit):
        ops.main(["run", "285", "stray"])


def test_a_bare_relative_window_is_made_a_systemd_relative_time():
    """journalctl rejects `--since 6h` ("Failed to parse timestamp"), found on the first live
    run. systemd wants a sign on a relative time, so a bare 6h becomes -6h."""
    assert "--since -6h" in ops.journal_command(since="6h", lines=10, grep=None)
    assert "--since -30m" in ops.journal_command(since="30m", lines=10, grep=None)


def test_an_absolute_timestamp_is_passed_through_untouched():
    cmd = ops.journal_command(since="2026-09-03 10:00", lines=10, grep=None)
    assert "'2026-09-03 10:00'" in cmd
    assert "-2026" not in cmd


def test_journal_grep_is_quoted():
    """A pattern reaches the remote shell quoted, so a pattern with a semicolon stays a
    pattern."""
    cmd = ops.journal_command(since="1h", lines=10, grep="a; rm -rf /")
    assert "; rm -rf /" not in cmd.replace("'a; rm -rf /'", "")


def test_remote_command_never_writes_to_the_volume_and_picks_the_project_image():
    cmd = ops.remote_command()
    assert "digest-newsroom" in cmd
    assert "--rm" in cmd and "-i" in cmd
    # Positive assertion: docker's default with no suffix is READ-WRITE, so checking for the
    # absence of ":rw" passed even with the :ro suffix deleted (found in review).
    assert f"-v {ops.VOLUME}:/d:ro " in cmd


def test_unknown_subcommand_exits_nonzero():
    with pytest.raises(SystemExit) as e:
        ops.main(["nonesuch"])
    assert e.value.code != 0
