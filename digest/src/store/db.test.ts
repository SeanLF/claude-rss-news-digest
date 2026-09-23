import net from "node:net";
import { describe, expect, it, vi } from "vitest";
import { openDb, utcText } from "./db.js";

// A stand-in Postgres: accepts a connection without a password, answers one simple query, then drops
// the connection while the client sits idle in the pool, as a Postgres restart does.
const msg = (type: string, body: Buffer) => {
  const b = Buffer.alloc(5 + body.length);
  b.write(type, 0);
  b.writeInt32BE(4 + body.length, 1);
  body.copy(b, 5);
  return b;
};
function droppingServer(): Promise<{ url: string; close: () => void }> {
  const server = net.createServer((sock) => {
    let started = false;
    sock.on("data", (d) => {
      if (!started) {
        started = true;
        sock.write(Buffer.concat([msg("R", Buffer.alloc(4)), msg("Z", Buffer.from("I"))]));
        return;
      }
      if (d[0] === 0x51) {
        sock.write(Buffer.concat([msg("C", Buffer.from("SELECT 0\0")), msg("Z", Buffer.from("I"))]));
        setTimeout(() => sock.destroy(), 50);
      }
    });
  });
  return new Promise((resolve) =>
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address() as net.AddressInfo;
      resolve({ url: `postgres://x@127.0.0.1:${port}/x?sslmode=disable`, close: () => server.close() });
    }),
  );
}

describe("openDb", () => {
  it("survives Postgres dropping an idle pooled connection, and says so", async () => {
    const warn = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const crashed = vi.fn();
    process.on("uncaughtException", crashed);
    const { url, close } = await droppingServer();
    try {
      await openDb(url).run("SELECT 1");
      await new Promise((r) => setTimeout(r, 300));
      expect(crashed).not.toHaveBeenCalled();
      expect(String(warn.mock.calls[0]?.[0])).toMatch(/idle Postgres connection/);
    } finally {
      process.off("uncaughtException", crashed);
      close();
      warn.mockRestore();
    }
  });
});

describe("utcText", () => {
  it("reads a timestamptz in any offset, and a plain timestamp as UTC, never as the host's zone", () => {
    expect(utcText("2026-09-18 10:25:40+00")).toBe("2026-09-18 10:25:40");
    expect(utcText("2026-09-18 06:25:40.123-04")).toBe("2026-09-18 10:25:40");
    expect(utcText("2026-09-18 10:25:40")).toBe("2026-09-18 10:25:40");
    expect(() => utcText("not a time")).toThrow(/not a timestamp/);
  });
});
