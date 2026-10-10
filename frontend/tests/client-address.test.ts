import { afterEach, describe, expect, it, vi } from "vitest";
import { clientAddress } from "@/lib/client-address";

const req = (headers: Record<string, string>) => new Request("http://web/api/v1/sessions", { headers });

describe("clientAddress", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("ignores platform headers outside Vercel (a client could forge them)", () => {
    expect(clientAddress(req({ "x-real-ip": "6.6.6.6", "x-forwarded-for": "6.6.6.6, 203.0.113.9" }))).toBe("203.0.113.9");
  });

  it("uses the platform header on Vercel", () => {
    vi.stubEnv("VERCEL", "1");
    expect(clientAddress(req({ "x-vercel-forwarded-for": "203.0.113.9", "x-forwarded-for": "6.6.6.6" }))).toBe("203.0.113.9");
  });

  it("takes the last forwarded entry, never the client-supplied first one", () => {
    expect(clientAddress(req({ "x-forwarded-for": "6.6.6.6, 203.0.113.9" }))).toBe("203.0.113.9");
  });

  it("rejects values that are not IP addresses", () => {
    expect(clientAddress(req({ "x-forwarded-for": "not-an-ip" }))).toBeNull();
    expect(clientAddress(req({ "x-forwarded-for": "1.2.3.4, <script>" }))).toBeNull();
  });

  it("returns null when nothing identifies the peer", () => {
    expect(clientAddress(req({}))).toBeNull();
  });
});
