-- Track the Resend broadcast per digest so delivery is idempotent and resumable.
--
-- On 2026-06-16 the broadcast send response read-timed-out AFTER Resend had
-- accepted the send. The run was retried by hand, and the only way to tell
-- whether the email had actually gone out (vs. needing a resend) was to query
-- Resend's broadcast status -- the broadcast_id existed only in the logs.
--
-- Persisting the broadcast id + status against the digest's DATE (the natural
-- idempotency key -- one digest per day) lets any retry/resume check "did today
-- already send?" and skip a second send, and lets recovery reuse the existing
-- draft instead of creating a duplicate broadcast. Both columns are nullable:
-- a digest with no broadcast row yet simply has NULLs. Additive, backward-safe.

ALTER TABLE digests ADD COLUMN broadcast_id TEXT;
ALTER TABLE digests ADD COLUMN broadcast_status TEXT;
