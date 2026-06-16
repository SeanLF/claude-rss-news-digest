-- Record how many subscribers a digest's broadcast reached, on the date-keyed
-- digests row alongside broadcast_id/status.
--
-- Without it, a resumed/idempotent run that SKIPS an already-sent broadcast can
-- only report articles_emailed=0, which reads as "delivered to nobody" -- the
-- exact metric ambiguity flagged in the 2026-06-16 review. Storing the count
-- per digest lets a skip report the real reach. Nullable; additive.

ALTER TABLE digests ADD COLUMN broadcast_recipients INTEGER;
