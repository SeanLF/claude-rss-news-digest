---
title: NULL columns and missing columns are different failures with different fixes
date: 2026-02-02
category: database-issues
module: db
problem_type: logic_error
severity: low
applies_when:
  - A column reads NULL and you are about to write a migration
  - Debugging "the data isn't there" in production
tags: [migrations, debugging, schema, deploy]
---

# NULL data and missing schema are different failures

## The lesson

Investigating why `articles_fetched` / `articles_kept` were NULL, the first
check was whether the columns existed. They did -- the migration had already
run. The real cause was that the *code* writing them had not been deployed yet.

Three distinguishable states that look identical from a query result:

| Observation | Actual cause | Fix |
|---|---|---|
| Column does not exist | Migration not applied | Migrate |
| Column exists, all NULL | Writer not deployed | Deploy |
| Column exists, NULL only for old rows | No backfill | Backfill |

## Guidance

Check the schema before writing a migration for a data problem. `pragma
table_info(<table>)` costs seconds and rules out an entire category of wrong
fix. Writing a migration for a deploy problem adds a no-op migration to the
permanent history and leaves the real bug in place.

## Related

- [[column-rename-needs-a-two-phase-deploy]]
- [[timestamp-written-before-it-is-read]]
