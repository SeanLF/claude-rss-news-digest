// JSON objects inside a model's free-text reply, as thread_synthesis reads them with
// json.JSONDecoder.raw_decode: a candidate starts at a "{" and ends at its matching "}", strings
// and escapes respected; whatever follows is ignored.

function objectEnd(text: string, start: number): number {
  let depth = 0;
  let inString = false;
  for (let i = start; i < text.length; i++) {
    const c = text[i];
    if (inString) {
      if (c === "\\") i++;
      else if (c === '"') inString = false;
    } else if (c === '"') inString = true;
    else if (c === "{") depth++;
    else if (c === "}" && --depth === 0) return i + 1;
  }
  return -1;
}

function decodeAt(text: string, start: number): { value: unknown; end: number } | undefined {
  const end = objectEnd(text, start);
  if (end < 0) return undefined;
  try {
    return { value: JSON.parse(text.slice(start, end)) as unknown, end };
  } catch {
    return undefined;
  }
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

// thread_synthesis._parse_json: the object that starts at the first "{", or a throw.
export function firstJsonObject(text: string): Record<string, unknown> {
  const start = text.indexOf("{");
  const got = start >= 0 ? decodeAt(text, start) : undefined;
  if (!got || !isObject(got.value)) throw new Error(`no JSON object in the reply, which began ${JSON.stringify(text.slice(0, 60))}`);
  return got.value;
}

// thread_synthesis._json_objects: every top-level object in the reply, in order; a "{" that does
// not open a valid object is prose and skipped.
export function jsonObjects(text: string): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = [];
  let i = text.indexOf("{");
  while (i >= 0) {
    const got = decodeAt(text, i);
    if (got && isObject(got.value)) {
      out.push(got.value);
      i = text.indexOf("{", got.end);
    } else i = text.indexOf("{", i + 1);
  }
  return out;
}
