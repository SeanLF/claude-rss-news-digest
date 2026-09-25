-- migrate:up

-- digest_ro (bin/ops, the site) reads every table, and every table a later migration adds. The
-- grants are the table owner's to give, so they come with the schema; the role is a superuser's to
-- create (db/ops/digest_ro.sql), before the worker first migrates. A database without the role
-- (PGlite, a scratch one) has nothing to grant to.
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'digest_ro') THEN
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO digest_ro;
    GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO digest_ro;
    -- For the objects the role running the migrations creates from here on.
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO digest_ro;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO digest_ro;
  END IF;
END $$;

-- migrate:down

DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'digest_ro') THEN
    ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON SEQUENCES FROM digest_ro;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM digest_ro;
    REVOKE SELECT ON ALL SEQUENCES IN SCHEMA public FROM digest_ro;
    REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM digest_ro;
  END IF;
END $$;
