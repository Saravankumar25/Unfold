import { afterEach, describe, expect, it, vi } from "vitest";
import { clientAddress } from "@/lib/client-address";

const req = (headers: Record<string, string>) => new Request("http://web/api/v1/sessions", { headers });

describe("clientAddress", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("ignores every client-supplied header by default (a client could forge them)", () => {
    expect(clientAddress(req({ "x-real-ip": "6.6.6.6", "x-forwarded-for": "6.6.6.6, 203.0.113.9" }))).toBeNull();
  });

  it("uses the platform header on Vercel", () => {
    vi.stubEnv("VERCEL", "1");
    expect(clientAddress(req({ "x-vercel-forwarded-for": "203.0.113.9", "x-forwarded-for": "6.6.6.6" }))).toBe("203.0.113.9");
  });

  it("does not fall back to X-Forwarded-For on Vercel without opt-in", () => {
    vi.stubEnv("VERCEL", "1");
    expect(clientAddress(req({ "x-forwarded-for": "6.6.6.6" }))).toBeNull();
  });

  it("takes the last forwarded entry, never the client-supplied first one, when opted in", () => {
    vi.stubEnv("TRUST_FORWARDED_FOR", "1");
    expect(clientAddress(req({ "x-forwarded-for": "6.6.6.6, 203.0.113.9" }))).toBe("203.0.113.9");
  });

  it("only the exact value 1 opts in", () => {
    vi.stubEnv("TRUST_FORWARDED_FOR", "true");
    expect(clientAddress(req({ "x-forwarded-for": "203.0.113.9" }))).toBeNull();
  });

  it("rejects values that are not IP addresses when opted in", () => {
    vi.stubEnv("TRUST_FORWARDED_FOR", "1");
    expect(clientAddress(req({ "x-forwarded-for": "not-an-ip" }))).toBeNull();
    expect(clientAddress(req({ "x-forwarded-for": "1.2.3.4, <script>" }))).toBeNull();
  });

  it("returns null when nothing identifies the peer", () => {
    vi.stubEnv("TRUST_FORWARDED_FOR", "1");
    expect(clientAddress(req({}))).toBeNull();
  });
});
