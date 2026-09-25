import http from "node:http";
import type { AddressInfo } from "node:net";
import { gzipSync } from "node:zlib";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { fetchBounded, isPublicAddress } from "./safe-fetch.js";

// 127.0.0.1 stands in for a public host and ::1 for an internal one: `isAllowed` admits only the
// former, and the fake resolver names each, so a refusal is observable as the ::1 listener's hits.
const isAllowed = (ip: string) => ip === "127.0.0.1";
const NAMES: Record<string, string> = { "feed.test": "127.0.0.1", "internal.test": "::1", "private.test": "10.0.0.5" };
const lookup = (host: string) => Promise.resolve(NAMES[host] ? [{ address: NAMES[host], family: NAMES[host].includes(":") ? 6 : 4 }] : []);

const RSS = "<rss><channel><title>T</title></channel></rss>";
const BOMB = gzipSync(Buffer.alloc(64 * 2 ** 20)); // 64 MiB of zeros, ~64 KiB on the wire

let pub: http.Server;
let internal: http.Server;
let internalHits = 0;
let feed = "";
let internalUrl = "";

const listen = (server: http.Server, host: string) => new Promise<number>((r) => server.listen(0, host, () => r((server.address() as AddressInfo).port)));

beforeAll(async () => {
  internal = http.createServer((_q, r) => {
    internalHits++;
    r.end("secret");
  });
  const ip = await listen(internal, "::1");
  internalUrl = `http://internal.test:${ip}/userdata`;
  pub = http.createServer((q, r) => {
    const hop = /^\/hop(\d)$/.exec(q.url ?? "");
    if (q.url === "/feed") return r.end(RSS);
    if (q.url === "/to-internal") return r.writeHead(302, { Location: internalUrl }).end();
    if (q.url === "/to-internal-ip") return r.writeHead(302, { Location: `http://[::1]:${ip}/userdata` }).end();
    if (q.url === "/to-private") return r.writeHead(302, { Location: "http://private.test/" }).end();
    if (q.url === "/to-file") return r.writeHead(302, { Location: "file:///etc/passwd" }).end();
    if (q.url === "/to-ftp") return r.writeHead(302, { Location: "ftp://feed.test/x" }).end();
    if (hop) return Number(hop[1]) > 0 ? r.writeHead(301, { Location: `/hop${Number(hop[1]) - 1}` }).end() : r.end(RSS);
    if (q.url === "/gzip") return r.writeHead(200, { "Content-Encoding": "gzip" }).end(gzipSync(RSS));
    if (q.url === "/mislabelled") return r.writeHead(200, { "Content-Encoding": "utf-8" }).end(RSS);
    if (q.url === "/truncated") return r.writeHead(200, { "Content-Encoding": "gzip" }).end(gzipSync(RSS).subarray(0, -8));
    if (q.url === "/bomb") return r.writeHead(200, { "Content-Encoding": "gzip" }).end(BOMB);
    if (q.url === "/drip") {
      r.writeHead(200);
      const t = setInterval(() => r.write("<"), 50);
      return r.on("close", () => clearInterval(t));
    }
    if (q.url === "/headers") return r.end(JSON.stringify(q.headers));
    return r.writeHead(404).end();
  });
  feed = `http://feed.test:${await listen(pub, "127.0.0.1")}`;
});
afterAll(() => {
  pub.closeAllConnections();
  pub.close();
  internal.close();
});

const opts = { timeoutMs: 5_000, maxBytes: 2 ** 20, isAllowed, lookup };

describe("fetchBounded", () => {
  it("fetches a feed through the checked resolver", async () => {
    expect(await fetchBounded(`${feed}/feed`, opts)).toEqual({ status: 200, body: RSS, finalUrl: `${feed}/feed` });
  });
  it("asks for compression and decodes it", async () => {
    expect(JSON.parse((await fetchBounded(`${feed}/headers`, { ...opts, headers: { "User-Agent": "UA" } })).body)).toMatchObject({ "accept-encoding": "gzip, deflate, br", "user-agent": "UA" });
    expect((await fetchBounded(`${feed}/gzip`, opts)).body).toBe(RSS);
  });
  it("reads a body as fetch would when its encoding is mislabelled or its gzip trailer is missing", async () => {
    expect((await fetchBounded(`${feed}/mislabelled`, opts)).body).toBe(RSS);
    expect((await fetchBounded(`${feed}/truncated`, opts)).body).toBe(RSS);
  });
  it("returns a non-2xx status for the caller to judge", async () => {
    expect((await fetchBounded(`${feed}/nope`, opts)).status).toBe(404);
  });
  // The control: with every address allowed the internal listener is reachable, so a zero below is a refusal.
  it("control: the internal listener is reachable when the check admits it", async () => {
    const before = internalHits;
    expect((await fetchBounded(`${feed}/to-internal`, { ...opts, isAllowed: () => true })).body).toBe("secret");
    expect(internalHits).toBe(before + 1);
  });
  it("refuses a redirect to a name resolving to an internal address, before connecting", async () => {
    const before = internalHits;
    await expect(fetchBounded(`${feed}/to-internal`, opts)).rejects.toThrow(/internal\.test.*::1/);
    await expect(fetchBounded(`${feed}/to-private`, opts)).rejects.toThrow(/10\.0\.0\.5/);
    await expect(fetchBounded(`${feed}/to-internal-ip`, opts)).rejects.toThrow(/::1/);
    expect(internalHits).toBe(before);
  });
  it("refuses a private address under the default check", async () => {
    const before = internalHits;
    await expect(fetchBounded(`${feed.replace("feed.test", "127.0.0.1")}/feed`, { timeoutMs: 5_000, maxBytes: 2 ** 20 })).rejects.toThrow(/127\.0\.0\.1/);
    await expect(fetchBounded(`${feed}/feed`, { timeoutMs: 5_000, maxBytes: 2 ** 20, lookup })).rejects.toThrow(/127\.0\.0\.1/);
    await expect(fetchBounded(internalUrl.replace("internal.test", "[::1]"), { timeoutMs: 5_000, maxBytes: 2 ** 20 })).rejects.toThrow(/::1/);
    expect(internalHits).toBe(before);
  });
  it("refuses any scheme but http and https, first hop or redirect", async () => {
    await expect(fetchBounded("file:///etc/passwd", opts)).rejects.toThrow(/scheme/);
    await expect(fetchBounded(`${feed}/to-file`, opts)).rejects.toThrow(/scheme file:/);
    await expect(fetchBounded(`${feed}/to-ftp`, opts)).rejects.toThrow(/scheme ftp:/);
  });
  it("follows two redirects and refuses a third", async () => {
    expect(await fetchBounded(`${feed}/hop2`, opts)).toEqual({ status: 200, body: RSS, finalUrl: `${feed}/hop0` });
    await expect(fetchBounded(`${feed}/hop3`, opts)).rejects.toThrow(/redirects/);
  });
  it("stops a decompression bomb at the cap without inflating all of it", async () => {
    const t = performance.now();
    const rss = process.memoryUsage().rss;
    await expect(fetchBounded(`${feed}/bomb`, opts)).rejects.toThrow(/exceeds 1048576 bytes/);
    expect(process.memoryUsage().rss - rss).toBeLessThan(32 * 2 ** 20); // the whole bomb is 64 MiB
    expect(performance.now() - t).toBeLessThan(2_000);
  });
  it("cuts a slow-drip body at the overall deadline", async () => {
    const t = performance.now();
    await expect(fetchBounded(`${feed}/drip`, { ...opts, timeoutMs: 300 })).rejects.toThrow(/timed out after 300 ms/);
    expect(performance.now() - t).toBeLessThan(1_500);
  });
});

describe("isPublicAddress", () => {
  const blocked = [
    "0.0.0.0", "0.1.2.3", "10.1.2.3", "100.64.0.1", "100.127.255.255", "127.0.0.1", "127.9.9.9", "169.254.169.254", "172.16.0.1", "172.31.255.255",
    "192.0.0.8", "192.0.2.1", "192.168.1.1", "198.18.0.1", "198.19.255.255", "198.51.100.7", "203.0.113.9", "224.0.0.1", "239.255.255.250", "240.0.0.1", "255.255.255.255",
    "::", "::1", "fc00::1", "fd12:3456::1", "fe80::1", "febf::1", "ff02::1", "2001:db8::1", "64:ff9b::a00:1", "100::1",
    "::ffff:127.0.0.1", "::ffff:7f00:1", "::ffff:10.0.0.5", "::ffff:a9fe:a9fe", "::127.0.0.1", "::ffff:0:0",
    "not-an-ip", "", "feed.test",
  ];
  const allowed = ["1.1.1.1", "8.8.8.8", "100.63.255.255", "100.128.0.1", "172.15.255.255", "172.32.0.1", "192.169.0.1", "198.20.0.1", "93.184.216.34", "2606:4700:4700::1111", "2a00:1450:4007::1", "::ffff:8.8.8.8", "::ffff:808:808"];
  it.each(blocked)("%s is not public", (ip) => expect(isPublicAddress(ip)).toBe(false));
  it.each(allowed)("%s is public", (ip) => expect(isPublicAddress(ip)).toBe(true));
});
