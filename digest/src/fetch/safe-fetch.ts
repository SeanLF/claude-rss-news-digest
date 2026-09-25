import dns, { type LookupAddress } from "node:dns";
import http from "node:http";
import https from "node:https";
import { BlockList, isIP, type LookupFunction } from "node:net";
import { pipeline } from "node:stream/promises";
import zlib from "node:zlib";

// A GET for URLs a third party controls (feed URLs and wherever they redirect). The worker shares a
// network with services that trust it, so every address a hop connects to is checked, inside the
// connection's own DNS lookup so no second resolution can differ from the checked one.

const blocked = new BlockList();
for (const [net, prefix] of [["0.0.0.0", 8], ["10.0.0.0", 8], ["100.64.0.0", 10], ["127.0.0.0", 8], ["169.254.0.0", 16], ["172.16.0.0", 12], ["192.0.0.0", 24], ["192.0.2.0", 24], ["192.168.0.0", 16], ["198.18.0.0", 15], ["198.51.100.0", 24], ["203.0.113.0", 24], ["224.0.0.0", 4], ["240.0.0.0", 4]] as const)
  blocked.addSubnet(net, prefix, "ipv4");
// Outside 2000::/3 (global unicast) covers ::, ::1, fc00::/7, fe80::/10, ff00::/8, 64:ff9b::/96 and 100::/64.
blocked.addSubnet("2001:db8::", 32, "ipv6");
const globalUnicast = new BlockList();
globalUnicast.addSubnet("2000::", 3, "ipv6");

// ::ffff:a.b.c.d (mapped) and ::a.b.c.d (compatible), in either spelling, as the IPv4 they carry.
const embeddedIpv4 = (ip: string): string | undefined => {
  const canonical = new URL(`http://[${ip}]/`).hostname.slice(1, -1); // dotted tails become hex groups
  const m = /^::(?:ffff:)?([0-9a-f]{1,4}):([0-9a-f]{1,4})$/.exec(canonical);
  if (!m) return undefined;
  const [hi, lo] = [parseInt(m[1]!, 16), parseInt(m[2]!, 16)];
  return [hi >> 8, hi & 255, lo >> 8, lo & 255].join(".");
};

export function isPublicAddress(ip: string): boolean {
  const family = isIP(ip);
  if (family === 4) return !blocked.check(ip, "ipv4");
  if (family !== 6) return false;
  const v4 = embeddedIpv4(ip);
  if (v4 !== undefined) return isPublicAddress(v4);
  return globalUnicast.check(ip, "ipv6") && !blocked.check(ip, "ipv6");
}

export interface BoundedOptions {
  timeoutMs: number; // one deadline for every hop and the body
  maxBytes: number; // after decompression
  maxRedirects?: number;
  headers?: Record<string, string>;
  isAllowed?: (ip: string) => boolean;
  lookup?: (hostname: string) => Promise<LookupAddress[]>;
}

export interface BoundedResponse { status: number; body: string; finalUrl: string }

const refused = (message: string) => Object.assign(new Error(message), { code: "EADDRNOTALLOWED" });

function send(url: URL, o: Required<Omit<BoundedOptions, "timeoutMs" | "maxBytes" | "maxRedirects">>, signal: AbortSignal): Promise<http.IncomingMessage> {
  if (url.protocol !== "http:" && url.protocol !== "https:") return Promise.reject(new Error(`refused scheme ${url.protocol} (${url.href})`));
  const host = url.hostname.replace(/^\[(.*)\]$/, "$1");
  // A literal address never reaches `lookup`.
  if (isIP(host) && !o.isAllowed(host)) return Promise.reject(refused(`refused ${url.host}: ${host} is not a public address`));
  const lookup: LookupFunction = (hostname, options, cb) => {
    o.lookup(hostname).then((all) => {
      const bad = all.find((a) => !o.isAllowed(a.address));
      if (bad) return cb(refused(`refused ${hostname}: resolves to ${bad.address}, not a public address`), "");
      const fit = options.family ? all.filter((a) => a.family === options.family) : all;
      if (fit.length === 0) return cb(Object.assign(new Error(`${hostname} did not resolve`), { code: "ENOTFOUND" }), "");
      if (options.all) cb(null, fit);
      else cb(null, fit[0]!.address, fit[0]!.family);
    }, (e: NodeJS.ErrnoException) => cb(e, ""));
  };
  return new Promise((resolve, reject) => {
    // No agent: a pooled socket would skip the lookup, and so the check.
    const req = (url.protocol === "https:" ? https : http).request(url, { headers: { Accept: "*/*", ...o.headers, "Accept-Encoding": "gzip, deflate, br" }, lookup, agent: false, signal }, resolve);
    req.on("error", reject);
    req.end();
  });
}

// Lenient as fetch's decoders are: a body missing its trailer still decodes what arrived.
const sync = { finishFlush: zlib.constants.Z_SYNC_FLUSH };
const DECODERS = new Map<string, () => zlib.Gunzip | zlib.Inflate | zlib.BrotliDecompress>([["gzip", () => zlib.createGunzip(sync)], ["x-gzip", () => zlib.createGunzip(sync)], ["deflate", () => zlib.createInflate(sync)], ["br", () => zlib.createBrotliDecompress({ finishFlush: zlib.constants.BROTLI_OPERATION_FLUSH })]]);

async function readBody(res: http.IncomingMessage, maxBytes: number, signal: AbortSignal): Promise<string> {
  const encoding = (res.headers["content-encoding"] ?? "identity").trim().toLowerCase();
  const decoder = DECODERS.get(encoding); // anything else (identity, "utf-8", stacked codings) is read raw, as fetch does
  const chunks: Buffer[] = [];
  let total = 0;
  const collect = async (source: AsyncIterable<Buffer>) => {
    for await (const chunk of source) {
      total += chunk.length;
      if (total > maxBytes) throw new Error(`body exceeds ${maxBytes} bytes after decoding`);
      chunks.push(chunk);
    }
  };
  if (decoder) await pipeline(res, decoder(), collect, { signal });
  else await pipeline(res, collect, { signal });
  return new TextDecoder().decode(Buffer.concat(chunks)); // as Response.text(): UTF-8, BOM dropped
}

export async function fetchBounded(url: string, options: BoundedOptions): Promise<BoundedResponse> {
  const { timeoutMs, maxBytes, maxRedirects = 2 } = options;
  const o = {
    headers: options.headers ?? {},
    isAllowed: options.isAllowed ?? isPublicAddress,
    lookup: options.lookup ?? ((h: string) => dns.promises.lookup(h, { all: true })),
  };
  const deadline = new AbortController();
  const timer = setTimeout(() => deadline.abort(), timeoutMs);
  try {
    let current = new URL(url);
    for (let hop = 0; ; hop++) {
      const res = await send(current, o, deadline.signal);
      const status = res.statusCode ?? 0;
      const location = res.headers.location;
      if (status >= 300 && status < 400 && location) {
        res.destroy();
        if (hop >= maxRedirects) throw new Error(`more than ${maxRedirects} redirects (${url})`);
        current = new URL(location, current);
        continue;
      }
      return { status, body: await readBody(res, maxBytes, deadline.signal), finalUrl: current.href };
    }
  } catch (e) {
    if (deadline.signal.aborted) throw new Error(`timed out after ${timeoutMs} ms (${url})`, { cause: e });
    throw e;
  } finally {
    clearTimeout(timer);
  }
}
