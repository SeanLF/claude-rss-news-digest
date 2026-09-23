import { describe, expect, it } from "vitest";
import { ArtifactStore } from "../store/artifacts.js";
import { freshDb } from "../store/test-db.js";
import { recordOperatorNote } from "./operator-note.js";

describe("recordOperatorNote", () => {
  it("persists each distinct note for a stage once, however often it arrives", async () => {
    const store = new ArtifactStore(await freshDb([300]));
    await recordOperatorNote(store, 300, "select", "prefer the Sudan story");
    await recordOperatorNote(store, 300, "select", "prefer the Sudan story"); // a retry of the same attempt
    await recordOperatorNote(store, 300, "select", "drop the celebrity item");
    await recordOperatorNote(store, 300, "write", "shorter summaries");
    const names = await store.names(300);
    expect(names.filter((n) => n.startsWith("operator_note.select."))).toHaveLength(2);
    expect(names.filter((n) => n.startsWith("operator_note.write."))).toHaveLength(1);
    expect((await Promise.all(names.map((n) => store.content(300, n)))).toSorted()).toEqual(["drop the celebrity item", "prefer the Sudan story", "shorter summaries"]);
  });
  it("records nothing without a note", async () => {
    const store = new ArtifactStore(await freshDb([300]));
    await recordOperatorNote(store, 300, "coherence", undefined);
    await recordOperatorNote(store, 300, "coherence", "  ");
    expect(await store.names(300)).toEqual([]);
  });
});
