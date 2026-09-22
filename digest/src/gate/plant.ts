import type { Selections } from "../contracts/selections.js";

// A defect planted in a draft before the checker runs (spec §7 item 1): ground truth by
// construction. wrong-number rewrites the first number in a field; a field with no number gets an
// invented specific appended.
export interface Plant {
  storyIndex: number;
  field: "headline" | "summary" | "why_it_matters";
  kind: "wrong-number" | "absent-specific";
  original: string;
  planted: string;
}

function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}
const NUMBER = /\d[\d,]*/;
const FIELDS = ["headline", "summary", "why_it_matters"] as const;

export function plantDefects(draft: Selections, seed: number, n: number): { draft: Selections; plants: Plant[] } {
  const next = rng(seed);
  const out: Selections = structuredClone(draft);
  const plants: Plant[] = [];
  const cells = out.must_know.length * FIELDS.length;
  if (n > cells) throw new Error(`asked for ${n} plants but the draft has ${cells} must_know fields`);
  while (plants.length < n) {
    const i = Math.floor(next() * out.must_know.length);
    const story = out.must_know[i];
    if (!story) continue;
    const field = FIELDS[Math.floor(next() * FIELDS.length)]!;
    if (plants.some((p) => p.storyIndex === i && p.field === field)) continue;
    const original = story[field];
    const m = NUMBER.exec(original);
    let planted: string;
    let kind: Plant["kind"];
    if (m) {
      const num = Number(m[0].replaceAll(",", ""));
      planted = original.replace(NUMBER, String(num * 2 + 1));
      kind = "wrong-number";
    } else {
      planted = `${original} The measure was announced in Geneva.`;
      kind = "absent-specific";
    }
    story[field] = planted;
    plants.push({ storyIndex: i, field, kind, original, planted });
  }
  return { draft: out, plants };
}
