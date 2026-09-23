import { describe, expect, it } from "vitest";
import { activeSources, isoUtc, newerThan, parseArticles } from "./feeds.js";

const RSS = `<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>T</title>
<item><title> Hi &amp; bye </title><link>https://x.test/a</link><pubDate>Thu, 18 Sep 2026 09:43:43 GMT</pubDate><description>&lt;b&gt;S&lt;/b&gt;</description><dc:creator>Reuters</dc:creator></item>
<item><title>No link</title></item></channel></rss>`;
const ATOM = `<feed xmlns="http://www.w3.org/2005/Atom"><title>A</title><entry><title>E</title><link rel="alternate" href="https://y.test/e"/><updated>2026-09-18T10:00:00Z</updated><summary>sum</summary><author><name>AP</name></author></entry></feed>`;

const a = (published: string | null) => ({ title: "t", url: "u", published, summary: "" });
const s = (id: string, extra = {}) => ({ id, name: id, url: "https://f.test/rss", bias: "center", factuality: "high", perspective: "global", ...extra });

describe("feeds", () => {
  it("maps RSS and Atom entries to the archive's shape, dropping entries without a link", () => {
    expect(parseArticles(RSS)).toEqual([{ title: "Hi & bye", url: "https://x.test/a", published: "2026-09-18T09:43:43+00:00", summary: "<b>S</b>", author: "Reuters" }]);
    expect(parseArticles(ATOM)).toEqual([{ title: "E", url: "https://y.test/e", published: "2026-09-18T10:00:00+00:00", summary: "sum", author: "AP" }]);
  });
  it("formats times as Python's UTC isoformat and keeps an unparseable one raw", () => {
    expect(isoUtc("2026-09-18T12:00:00+02:00")).toBe("2026-09-18T10:00:00+00:00");
    expect(isoUtc("yesterday-ish")).toBe("yesterday-ish");
  });
  it("keeps what is newer than the last completed run, and undated entries", () => {
    expect(newerThan([a("2026-09-18T09:00:00+00:00"), a("2026-09-18T11:00:00+00:00"), a(null)], "2026-09-18 10:25:40").map((x) => x.published)).toEqual(["2026-09-18T11:00:00+00:00", null]);
  });
  it("validates the catalogue and leaves parked sources out", () => {
    expect(activeSources([s("a"), s("b", { active: false, inactive_reason: "blocks our ASN" })]).map((x) => x.id)).toEqual(["a"]);
    expect(() => activeSources([s("c", { active: false })])).toThrow(/inactive_reason/);
    expect(() => activeSources([s("Bad-Id")])).toThrow(/invalid id/);
  });
});
