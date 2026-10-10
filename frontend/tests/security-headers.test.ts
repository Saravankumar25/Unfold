import { describe, expect, it } from "vitest";
import nextConfig from "../next.config";

describe("next.config headers", () => {
  it("sends CSP, HSTS and Permissions-Policy on every path", async () => {
    const rules = await nextConfig.headers!();
    const all = rules.find((r) => r.source === "/:path*");
    const keys = (all?.headers ?? []).map((h) => h.key);
    expect(keys).toEqual(
      expect.arrayContaining(["Content-Security-Policy", "Strict-Transport-Security", "Permissions-Policy"]),
    );
  });
});
