import { describe, expect, it } from "vitest";
import { clientAddress } from "@/lib/client-address";

const req = (headers: Record<string, string>) => new Request("http://web/api/v1/sessions", { headers });

describe("clientAddress", () => {
  it("uses the platform header over anything the client sent", () => {
    expect(clientAddress(req({ "x-vercel-forwarded-for": "203.0.113.9", "x-forwarded-for": "6.6.6.6" }))).toBe("203.0.113.9");
  });

  it("takes the last forwarded entry, never the client-supplied first one", () => {
    expect(clientAddress(req({ "x-forwarded-for": "6.6.6.6, 203.0.113.9" }))).toBe("203.0.113.9");
  });

  it("returns null when nothing identifies the peer", () => {
    expect(clientAddress(req({}))).toBeNull();
  });
});
