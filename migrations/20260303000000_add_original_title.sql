-- Add original_title to shown_narratives for RSS-title-based dedup
-- Stores the RSS title alongside the editorial headline

ALTER TABLE shown_narratives ADD COLUMN original_title TEXT;
