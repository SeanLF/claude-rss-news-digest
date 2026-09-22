// One judge cell: a story, a rubric criterion (spec §7.1, numbered 1-7), pass or fail, one reason.
export type Criterion = 1 | 2 | 3 | 4 | 5 | 6 | 7;
export interface JudgeVerdict {
  story: number;
  criterion: Criterion;
  pass: boolean;
  reason: string;
}

// The outermost [...] in a judge's reply, validated cell by cell.
export function parseVerdicts(text: string): JudgeVerdict[] {
  const start = text.indexOf("[");
  const end = text.lastIndexOf("]");
  if (start < 0 || end < start) throw new Error("judge returned no JSON array");
  const parsed: unknown = JSON.parse(text.slice(start, end + 1));
  if (!Array.isArray(parsed)) throw new Error("judge returned no JSON array");
  return parsed.map((raw: unknown, i) => {
    const o = (raw ?? {}) as Record<string, unknown>;
    const { story, criterion, pass, reason } = o;
    if (typeof story !== "number" || typeof criterion !== "number" || criterion < 1 || criterion > 7 || typeof pass !== "boolean")
      throw new Error(`judge verdict ${i} is malformed: ${JSON.stringify(raw)}`);
    return { story, criterion: criterion as Criterion, pass, reason: typeof reason === "string" ? reason : "" };
  });
}
