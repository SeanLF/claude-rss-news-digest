---
title: ALTER TABLE RENAME COLUMN is not zero-downtime safe, because migrations run after code deploys
date: 2026-02-06
category: database-issues
module: db, migrations
problem_type: schema_change
severity: medium
applies_when:
  - Renaming or dropping a column referenced by running code
  - Any schema change where old and new code overlap in production
tags: [migrations, schema, zero-downtime, expand-contract, sqlite]
---

# A column rename needs a two-phase deploy

## The problem

Migrations run **after** the new code deploys. That leaves a window where the
new code references a column that does not exist yet. `ALTER TABLE ... RENAME
COLUMN` closes the old name and opens the new one atomically, so there is no
moment where both work.

Caught in review before deploy, not in production.

## The safe pattern (expand / contract)

1. **Expand** -- add the new column, backfill it, update code to write *both*.
2. Deploy and let it settle.
3. **Contract** -- drop the old column once every reader uses the new one.

## When the simple rename is fine

Judgment, not dogma. The `articles_fetched` to `articles_kept` rename
(migration `20260619000000`) used a single `RENAME COLUMN` deliberately: the
only reader was an internal one-viewer `/stats` dashboard, and `RENAME`
preserves all historical values where a drop-and-recompute would not. A brief
blip on an internal page does not justify a two-phase migration.

State the blast radius, then choose. Expand/contract is the default for
anything subscriber-facing or multi-reader.

## Related

- [[null-data-and-missing-schema-are-different-failures]]
