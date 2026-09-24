import { describe, expect, it } from "vitest";
import { sameDocument } from "./document.js";
import { type Answer, type Entry, capture, compare, manifest, toRequest } from "./harness.js";

// The harness's negative control: a comparator that passes everything is indistinguishable from a
// port with full parity, so every contract is shown failing on a deliberately changed answer.
const answer = (over: Partial<Answer> = {}): Answer => ({
  status: 200,
  headers: { "content-type": "text/plain; charset=utf-8" },
  body: "hello\nworld\n",
  encoding: "utf8",
  ...over,
});
const entry = (contract: Entry["compare"]): Entry => ({ name: "t", path: "/", compare: contract });
const toolAnswer = (lines: string[]): Answer =>
  answer({ headers: { "content-type": "application/json" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, result: { content: [{ type: "text", text: `# Headline search\n\n${lines.length} results, most relevant first.\n\n${lines.join("\n")}\n` }] } }) });

describe("the comparator passes what is equal", () => {
  it.each(["body", "json", "markdown", "headers"] as const)("%s", (c) => {
    const a = c === "json" ? answer({ body: '{"a":1,"b":[1,2]}' }) : answer();
    expect(compare(entry(c), a, structuredClone(a)).ok).toBe(true);
  });

  it("ignores how a header is spelled, not what it says", () => {
    const g = answer({ headers: { "content-type": "text/html; charset=utf-8", vary: "accept" } });
    const a = answer({ headers: { "content-type": "text/html;charset=UTF-8", vary: "Accept" } });
    expect(compare(entry("headers"), g, a).ok).toBe(true);
  });

  it("compares JSON as values, not as key order", () => {
    expect(compare(entry("json"), answer({ body: '{"a":1,"b":2}' }), answer({ body: '{"b":2,"a":1}' })).ok).toBe(true);
  });

  it("compares a capped search by its count: two rankings choose different fifties", () => {
    const v = compare(entry("search"), toolAnswer(Array.from({ length: 50 }, (_, i) => `- a${i}`)), toolAnswer(Array.from({ length: 50 }, (_, i) => `- b${i}`)));
    expect(v.ok).toBe(true);
    expect(v.overlap).toEqual({ golden: 50, actual: 50, shared: 0 });
  });

  it("carries a known divergence's reason on the verdict", () => {
    expect(compare({ ...entry("body"), known: "ts_rank" }, answer(), answer({ body: "x" }))).toMatchObject({ ok: false, known: "ts_rank" });
  });

  it("compares search answers as result sets, not order", () => {
    const v = compare(entry("search"), toolAnswer(["- 2026-09-01 · must know · A", "- 2026-08-01 · should know · B"]), toolAnswer(["- 2026-08-01 · should know · B", "- 2026-09-01 · must know · A"]));
    expect(v.ok).toBe(true);
    expect(v.overlap).toEqual({ golden: 2, actual: 2, shared: 2 });
  });
});

describe("the comparator fails a deliberately changed answer (negative control)", () => {
  it.each([
    ["status", entry("headers"), answer(), answer({ status: 404 })],
    ["a compared header's value", entry("headers"), answer({ headers: { "content-type": "text/plain", location: "/issues/2026-09-01" } }), answer({ headers: { "content-type": "text/plain", location: "/issues/2026-09-02" } })],
    ["a compared header missing", entry("headers"), answer({ headers: { "content-type": "text/plain", vary: "accept" } }), answer({ headers: { "content-type": "text/plain" } })],
    ["a compared header added", entry("headers"), answer(), answer({ headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" } })],
    ["one byte of a body", entry("body"), answer(), answer({ body: "hello\nworld!\n" })],
    ["a trailing newline", entry("body"), answer(), answer({ body: "hello\nworld" })],
    ["a binary body", entry("body"), answer({ body: "AAEC", encoding: "base64" }), answer({ body: "AAED", encoding: "base64" })],
    ["one markdown line", entry("markdown"), answer({ body: "# T\n\n- a\n" }), answer({ body: "# T\n\n* a\n" })],
    ["a JSON value", entry("json"), answer({ body: '{"a":1}' }), answer({ body: '{"a":2}' })],
    ["a JSON key added", entry("json"), answer({ body: '{"a":1}' }), answer({ body: '{"a":1,"b":null}' })],
    ["unparseable JSON against JSON", entry("json"), answer({ body: '{"a":1}' }), answer({ body: "{a:1}" })],
    ["a search result missing", entry("search"), toolAnswer(["- x", "- y"]), toolAnswer(["- x"])],
    ["an empty search on one side", entry("search"), toolAnswer(["- x"]), toolAnswer([])],
    // Found by review: counting the golden's duplicates let two extra results through.
    ["extra search results hidden behind the golden's duplicates", entry("search"), toolAnswer(["- x", "- x", "- x", "- y"]), toolAnswer(["- x", "- y", "- z", "- w"])],
    ["a capped search against an uncapped one", entry("search"), toolAnswer(Array.from({ length: 50 }, (_, i) => `- r${i}`)), toolAnswer(Array.from({ length: 49 }, (_, i) => `- r${i}`))],
    ["a 405 without its allow header", entry("headers"), answer({ status: 405, headers: { allow: "GET,HEAD" } }), answer({ status: 405, headers: {} })],
  ] as [string, Entry, Answer, Answer][])("%s", (_what, e, g, a) => {
    const v = compare(e, g, a);
    expect(v.ok).toBe(false);
    expect(v.diffs.length).toBeGreaterThan(0);
  });

  it("headers-only still fails on status, whatever the bodies", () => {
    expect(compare(entry("headers"), answer({ body: "a" }), answer({ body: "b" })).ok).toBe(true);
    expect(compare(entry("headers"), answer({ body: "a" }), answer({ status: 500, body: "a" })).ok).toBe(false);
  });
});

describe("Markdown compared as a document", () => {
  const spelled = ["# T\n\n* a\n* b\n\n**Why it matters** \n\nBody. \n", "# T\n\n- a\n- b\n\n**Why it matters**\n\nBody.\n"] as const;

  it("passes a respelling that renders the same, and says so", () => {
    const v = compare(entry("markdown"), answer({ body: spelled[0] }), answer({ body: spelled[1] }), sameDocument);
    expect(v).toMatchObject({ ok: true, asDocument: true });
    const inJson = (text: string) => answer({ body: JSON.stringify({ result: { content: [{ text }] } }) });
    expect(compare(entry("json"), inJson(spelled[0]), inJson(spelled[1]), sameDocument)).toMatchObject({ ok: true, asDocument: true });
  });

  it("fails without the renderer: bytes are the default contract", () => {
    expect(compare(entry("markdown"), answer({ body: spelled[0] }), answer({ body: spelled[1] })).ok).toBe(false);
  });

  it.each([
    ["a word", "# T\n\nBody one.\n", "# T\n\nBody two.\n"],
    ["a block's kind", "# T\n\nBody.\n", "## T\n\nBody.\n"],
    ["a list item merged", "* a\n* b\n", "* a b\n"],
    ["a link target", "[x](https://a.example)\n", "[x](https://b.example)\n"],
    ["a table cell", "| a | b |\n| - | - |\n| 1 | 2 |\n", "| a | b |\n| - | - |\n| 1 | 3 |\n"],
    ["a space between words", "one two\n", "onetwo\n"],
  ])("still fails on %s", (_what, a, b) => {
    expect(compare(entry("markdown"), answer({ body: a }), answer({ body: b }), sameDocument).ok).toBe(false);
  });
});

describe("the manifest", () => {
  const m = manifest();

  it("names every request once", () => {
    const names = m.requests.map((r) => r.name);
    expect(new Set(names).size).toBe(names.length);
  });

  it("covers every tool over JSON-RPC and over the GET bridge", () => {
    const tools = ["get_latest_issue", "get_issue", "list_issues", "search_headlines", "list_threads", "get_thread", "get_sources", "get_stats"];
    const called = new Set(m.requests.flatMap((r) => (r.rpc?.method === "tools/call" ? [(r.rpc.params as { name: string }).name] : [])));
    const bridged = new Set(m.requests.flatMap((r) => (r.path?.startsWith("/mcp/tools/") ? [r.path.slice("/mcp/tools/".length).split(".json")[0]!] : [])));
    for (const t of tools) {
      expect(called).toContain(t);
      expect(bridged).toContain(t);
    }
  });

  it("builds an rpc entry as the POST an MCP client sends", async () => {
    const e = m.requests.find((r) => r.name === "rpc-tools-list")!;
    const req = toRequest("http://site", m, e, "/f.woff2");
    expect(req.method).toBe("POST");
    expect(new URL(req.url).pathname).toBe("/mcp");
    expect(req.headers.get("accept")).toBe("application/json, text/event-stream");
    expect(JSON.parse(await req.text())).toEqual({ jsonrpc: "2.0", id: 1, method: "tools/list" });
  });

  it("fills the placeholders only the server can know", () => {
    const font = toRequest("http://site", m, m.requests.find((r) => r.name === "font")!, "/assets/fonts/x.woff2");
    expect(new URL(font.url).pathname).toBe("/assets/fonts/x.woff2");
    const long = toRequest("http://site", m, m.requests.find((r) => r.name === "bridge-issue-too-long")!, "");
    expect(new URL(long.url).searchParams.get("date")).toHaveLength(201);
  });
});

describe("capture", () => {
  it("keeps binary bodies as base64 and only the compared headers", async () => {
    const res = new Response(new Uint8Array([0x89, 0x50]), { headers: { "content-type": "image/png", "cache-control": "max-age=1", date: "now" } });
    expect(await capture(res)).toEqual({ status: 200, headers: { "content-type": "image/png", "cache-control": "max-age=1" }, body: "iVA=", encoding: "base64" });
  });
});
