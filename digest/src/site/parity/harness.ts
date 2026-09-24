import { isDeepStrictEqual } from "node:util";
import { MANIFEST } from "./requests.js";

// The parity harness (docs/2026-09-23-web-tier-typescript-fork.md §5): the same requests go to the Rust
// server and to the TypeScript app, and each answer is held to its contract. Rust answers are recorded
// once (cli/site-parity.ts) as goldens; the TypeScript side is compared against them in-process.

export type Contract = "body" | "json" | "markdown" | "headers";
export interface Entry {
  name: string;
  method?: "GET" | "POST" | "DELETE" | "HEAD";
  path?: string;
  headers?: Record<string, string>;
  body?: string;
  compare: Contract;
  // Sent this many times, the last answer recorded: a rate limit's refusal is the answer after the quota.
  repeat?: number;
  // A difference this port makes on purpose (fork doc §5), with the reason. Still compared and reported;
  // the parity gate counts it apart from failures.
  known?: string;
}
export interface Manifest {
  headers: Record<string, string>;
  requests: Entry[];
}
export interface Answer {
  status: number;
  headers: Record<string, string>;
  body: string;
  encoding: "utf8" | "base64";
}
export interface Golden extends Answer {
  name: string;
}

export const manifest = (): Manifest => MANIFEST;

// Headers the contracts cover. Everything else a server adds (date, content-length, the new security
// headers) is outside the comparison.
export const COMPARED_HEADERS = ["content-type", "location", "cache-control", "link", "vary", "allow"] as const;

// The request an entry describes. `{font}` is the hashed font path, which only the server knows.
export function toRequest(base: string, m: Manifest, e: Entry, fontPath: string): Request {
  const headers = new Headers({ ...m.headers, ...e.headers });
  const path = (e.path ?? "/").replace("{font}", fontPath);
  const method = e.method ?? "GET";
  return new Request(`${base}${path}`, { method, headers, redirect: "manual", ...(e.body === undefined ? {} : { body: e.body }) });
}

const BINARY = /^(image|font)\//;
export async function capture(res: Response): Promise<Answer> {
  const headers: Record<string, string> = {};
  for (const h of COMPARED_HEADERS) {
    const v = res.headers.get(h);
    if (v !== null) headers[h] = v;
  }
  const bytes = Buffer.from(await res.arrayBuffer());
  const binary = BINARY.test(res.headers.get("content-type") ?? "");
  return { status: res.status, headers, body: bytes.toString(binary ? "base64" : "utf8"), encoding: binary ? "base64" : "utf8" };
}

// Spelling a header differently is not a difference: `text/html; charset=utf-8` against
// `text/html;charset=UTF-8`, or `Accept` against `accept` in vary.
function normaliseHeader(name: string, v: string): string {
  if (name === "content-type") return v.toLowerCase().replaceAll(/\s*;\s*/g, "; ");
  if (name === "vary") return v.toLowerCase().split(",").map((s) => s.trim()).toSorted().join(", ");
  return v;
}

export interface Verdict {
  name: string;
  ok: boolean;
  known?: string;
  diffs: string[];
  // Equal only as Markdown documents: the bytes differ, the rendering does not.
  asDocument?: boolean;
}

// Whether two Markdown texts are the same document (they render the same). The harness takes it from
// its caller: the renderer is a test dependency, and the recorder in the production image has none.
export type SameDocument = (a: string, b: string) => boolean;
const bytesOnly: SameDocument = () => false;

function firstDifference(a: string, b: string): string {
  const la = a.split("\n");
  const lb = b.split("\n");
  for (let i = 0; i < Math.max(la.length, lb.length); i++) {
    if (la[i] !== lb[i]) return `line ${i + 1}: rust ${JSON.stringify(la[i] ?? null)} | ts ${JSON.stringify(lb[i] ?? null)}`;
  }
  return "equal line by line (a trailing difference)";
}

// The first path at which two JSON values differ, with both sides; strings are compared line by line.
// A string leaf that differs only as Markdown spelling counts as equal and is recorded in `asDocument`.
export function jsonDifference(a: unknown, b: unknown, path = "$", same: SameDocument = bytesOnly, asDocument: string[] = []): string | undefined {
  if (isDeepStrictEqual(a, b)) return undefined;
  if (typeof a === "string" && typeof b === "string") {
    if (same(a, b)) {
      asDocument.push(path);
      return undefined;
    }
    return `${path}: ${firstDifference(a, b)}`;
  }
  if (a && b && typeof a === "object" && typeof b === "object" && Array.isArray(a) === Array.isArray(b)) {
    const ao = a as Record<string, unknown>;
    const bo = b as Record<string, unknown>;
    for (const k of new Set([...Object.keys(ao), ...Object.keys(bo)])) {
      if (!(k in ao) || !(k in bo)) return `${path}.${k}: ${k in ao ? "missing in ts" : "extra in ts"}`;
      const d = jsonDifference(ao[k], bo[k], `${path}.${k}`, same, asDocument);
      if (d) return d;
    }
    // Every member equal, some only as documents.
    return undefined;
  }
  return `${path}: rust ${JSON.stringify(a)?.slice(0, 200)} | ts ${JSON.stringify(b)?.slice(0, 200)}`;
}

const parseOrKeep = (s: string): unknown => {
  try {
    return JSON.parse(s);
  } catch {
    return { unparseable: s };
  }
};

export function compare(e: Entry, golden: Answer, actual: Answer, same: SameDocument = bytesOnly): Verdict {
  const diffs: string[] = [];
  const asDocument: string[] = [];
  if (golden.status !== actual.status) diffs.push(`status ${golden.status} != ${actual.status}`);
  for (const h of COMPARED_HEADERS) {
    const g = golden.headers[h];
    const a = actual.headers[h];
    if ((g === undefined) !== (a === undefined) || (g !== undefined && a !== undefined && normaliseHeader(h, g) !== normaliseHeader(h, a))) {
      diffs.push(`${h}: ${JSON.stringify(g ?? null)} != ${JSON.stringify(a ?? null)}`);
    }
  }
  switch (e.compare) {
    case "headers":
      // The body is judged on its requirement plus a11y and Lighthouse (fork doc §1), not here.
      break;
    case "body":
    case "markdown":
      if (golden.encoding !== actual.encoding) diffs.push(`encoding ${golden.encoding} != ${actual.encoding}`);
      else if (golden.body === actual.body) break;
      else if (e.compare === "markdown" && same(golden.body, actual.body)) asDocument.push("$");
      else diffs.push(`body: ${golden.encoding === "base64" ? `${golden.body.length} != ${actual.body.length} base64 chars or content` : firstDifference(golden.body, actual.body)}`);
      break;
    case "json": {
      // As values: a whole float that serde_json prints as 1.0 and JavaScript as 1 is the same value
      // (fork doc §5).
      const g = parseOrKeep(golden.body);
      const a = parseOrKeep(actual.body);
      const d = jsonDifference(g, a, "$", same, asDocument);
      if (d) diffs.push(`json ${d}`);
      break;
    }
  }
  return { name: e.name, ok: diffs.length === 0, diffs, ...(e.known ? { known: e.known } : {}), ...(diffs.length === 0 && asDocument.length ? { asDocument: true } : {}) };
}
