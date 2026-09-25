-- digest_ro: the read-only role bin/ops (and bin/db-clone --live) and the site log in as. Idempotent.
-- Run it as a superuser, connected to the database it reads (digest). Creating the role is a
-- superuser's (on the box, seanfloyd-infra pg-roles); the reads on the tables, now and to come, are
-- the table owner's, given by the migration 20260925120000_digest_ro_grants where the role exists.
-- No password, so it logs in only where pg_hba trusts: the container's socket and its own loopback,
-- never the network.
SELECT 'CREATE ROLE digest_ro' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'digest_ro')\gexec
ALTER ROLE digest_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD NULL;
-- The session guarantee, held by the role as well as by the PGOPTIONS bin/ops sends. A role setting
-- can be SET off by the session; the grants cannot.
ALTER ROLE digest_ro SET default_transaction_read_only = on;
-- A read must not hold Temporal's Postgres (384 MiB, shared) for long. pg_dump sets its own 0.
ALTER ROLE digest_ro SET statement_timeout = '60s';
SELECT format('GRANT CONNECT ON DATABASE %I TO digest_ro', current_database())\gexec
GRANT USAGE ON SCHEMA public TO digest_ro;
-- The tables already there, again: make dev-import restores a clone without grants (bin/db-clone,
-- --no-acl) whose history already holds the grants migration.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO digest_ro;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO digest_ro;
