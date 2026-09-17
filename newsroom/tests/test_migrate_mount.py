"""bin/migrate reads the tree's migrations, not the image's copy of them."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATE = ROOT / "bin/migrate"
COMPOSE = ROOT / "docker-compose.yml"

MOUNT = re.compile(r'-v\s+"\$PROJECT_DIR/migrations:/app/migrations:ro"')


def test_migrate_mounts_the_host_migrations_dir() -> None:
    """migrations/ is COPY'd at image build. Without this mount every bin/migrate
    subcommand reads the directory as it stood at the last build: `--status` omits a
    migration added since, and a bare `bin/migrate` exits 0 saying none are pending."""
    assert MOUNT.search(MIGRATE.read_text()), "bin/migrate must mount the host migrations dir"


def test_the_mount_is_scoped_to_migrate() -> None:
    """Not in compose, deliberately. src/ is baked into the image, so live-mounting
    migrations for every `docker compose run digest-newsroom` would let a plain pipeline
    run auto-apply a migration the running code predates -- the paired schema+code change
    in docs/lessons/database-issues/column-rename-needs-a-two-phase-deploy.md, in a new
    place. Keeping it here means the two only ever diverge for the tool that reports on
    them, never for the one that runs against them."""
    assert "/app/migrations" not in COMPOSE.read_text()
