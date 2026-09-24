-- migrate:up

-- An issue as Markdown, the body under the site's title line, for agents (`.md`, Accept:
-- text/markdown). The pipeline writes it at saveDigest from the run's selections; the issues published
-- before that are filled once from their stored HTML (cli/backfill-markdown). NULL is an issue with
-- neither, which the site 404s as Markdown and still serves as a page.
ALTER TABLE issues ADD COLUMN markdown text;

-- migrate:down

ALTER TABLE issues DROP COLUMN markdown;
