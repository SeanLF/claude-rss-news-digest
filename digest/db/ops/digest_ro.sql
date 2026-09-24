-- digest_ro: the read-only role bin/ops (and bin/db-clone --live) log in as on the box. Idempotent.
-- Run it as postgres, connected to the database it reads (digest), after every start and after the
-- import renames digest_import over digest: the grants belong to the database and its tables.
-- No password, so it logs in only where pg_hba trusts: the container's socket and its own loopback,
-- never the network. The schema must exist first (the next line fails without it), or the default
-- privileges below would grant nothing, silently.
SELECT 'runs'::regclass;
SELECT 'CREATE ROLE digest_ro' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'digest_ro')\gexec
ALTER ROLE digest_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD NULL;
-- The session guarantee, held by the role as well as by the PGOPTIONS bin/ops sends. A role setting
-- can be SET off by the session; the grants below cannot.
ALTER ROLE digest_ro SET default_transaction_read_only = on;
-- A read must not hold Temporal's Postgres (384 MiB, shared) for long. pg_dump sets its own 0.
ALTER ROLE digest_ro SET statement_timeout = '60s';
SELECT format('GRANT CONNECT ON DATABASE %I TO digest_ro', current_database())\gexec
GRANT USAGE ON SCHEMA public TO digest_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO digest_ro;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO digest_ro;
-- Tables a later migration creates, as whoever owns the schema's tables (digest on the box).
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT ON TABLES TO digest_ro', tableowner)
  FROM pg_tables WHERE schemaname = 'public' AND tablename = 'runs'\gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT ON SEQUENCES TO digest_ro', tableowner)
  FROM pg_tables WHERE schemaname = 'public' AND tablename = 'runs'\gexec
