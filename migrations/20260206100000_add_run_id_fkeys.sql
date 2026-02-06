-- Add run_id foreign keys to link all table rows back to their digest run

ALTER TABLE shown_narratives ADD COLUMN run_id INTEGER REFERENCES digest_runs(id);
ALTER TABLE source_health ADD COLUMN run_id INTEGER REFERENCES digest_runs(id);
ALTER TABLE dedup_log ADD COLUMN run_id INTEGER REFERENCES digest_runs(id);
ALTER TABLE digests ADD COLUMN run_id INTEGER REFERENCES digest_runs(id);

CREATE INDEX idx_shown_narratives_run ON shown_narratives(run_id);
CREATE INDEX idx_source_health_run ON source_health(run_id);
CREATE INDEX idx_dedup_log_run ON dedup_log(run_id);
