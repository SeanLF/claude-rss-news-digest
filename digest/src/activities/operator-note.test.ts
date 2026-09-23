import { describe, expect, it } from "vitest";
import { ArtifactStore } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import { recordOperatorNote } from "./operator-note.js";

describe("recordOperatorNote", () => {
  it("persists each distinct note for a stage once, however often it arrives", () => {
    const store = new ArtifactStore(freshDb([300]));
    recordOperatorNote(store, 300, "select", "prefer the Sudan story");
    recordOperatorNote(store, 300, "select", "prefer the Sudan story"); // a retry of the same attempt
    recordOperatorNote(store, 300, "select", "drop the celebrity item");
    recordOperatorNote(store, 300, "write", "shorter summaries");
    const names = store.names(300);
    expect(names.filter((n) => n.startsWith("operator_note.select."))).toHaveLength(2);
    expect(names.filter((n) => n.startsWith("operator_note.write."))).toHaveLength(1);
    expect(names.map((n) => store.get(store.find(300, n)!)).toSorted()).toEqual(["drop the celebrity item", "prefer the Sudan story", "shorter summaries"]);
  });
  it("records nothing without a note", () => {
    const store = new ArtifactStore(freshDb([300]));
    recordOperatorNote(store, 300, "coherence", undefined);
    recordOperatorNote(store, 300, "coherence", "  ");
    expect(store.names(300)).toEqual([]);
  });
});
