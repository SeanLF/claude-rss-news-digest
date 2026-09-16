-- A merged thread keeps its row and points at the survivor; sent /thread/{id} links resolve
-- through it instead of 404ing.
ALTER TABLE threads ADD COLUMN merged_into INTEGER REFERENCES threads(id);
