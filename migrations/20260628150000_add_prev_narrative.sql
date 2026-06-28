-- Sub-project C tune: keep the PRIOR run's narrative so the "story so far" line renders as
-- background context (where the story stood coming into today), not a restatement of today's
-- development -- which the current narrative includes and the day's summary already covers.

ALTER TABLE threads ADD COLUMN prev_narrative TEXT;
