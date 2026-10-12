import { afterEach, describe, expect, it, vi } from "vitest";
import nextConfig from "../next.config";

async function connectSrc(): Promise<string> {
  const { default: config } = await import("../next.config");
  const rules = await config.headers!();
  const csp = rules.find((r) => r.source === "/:path*")!.headers.find((h) => h.key === "Content-Security-Policy")!.value;
  return csp.split("; ").find((d) => d.startsWith("connect-src"))!;
}

describe("next.config headers", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("sends CSP, HSTS and Permissions-Policy on every path", async () => {
    const rules = await nextConfig.headers!();
    const all = rules.find((r) => r.source === "/:path*");
    const keys = (all?.headers ?? []).map((h) => h.key);
    expect(keys).toEqual(
      expect.arrayContaining(["Content-Security-Policy", "Strict-Transport-Security", "Permissions-Policy"]),
    );
  });

  it("pins connect-src to the upload bucket host when UPLOAD_BUCKET_HOST is set", async () => {
    vi.stubEnv("UPLOAD_BUCKET_HOST", "unfold-up.s3.eu-west-1.amazonaws.com");
    vi.resetModules();
    const src = await connectSrc();
    expect(src).toContain("https://unfold-up.s3.eu-west-1.amazonaws.com");
    expect(src).not.toContain("*.amazonaws.com");
  });

  it("falls back to the wildcard outside production, and to no S3 origin in production", async () => {
    vi.stubEnv("UPLOAD_BUCKET_HOST", "");
    vi.resetModules();
    expect(await connectSrc()).toContain("https://*.amazonaws.com");
    vi.stubEnv("NODE_ENV", "production");
    vi.resetModules();
    expect(await connectSrc()).toBe("connect-src 'self'");
  });

  it("rejects a value that is not a bare amazonaws.com host", async () => {
    vi.stubEnv("UPLOAD_BUCKET_HOST", "evil.example.com; script-src *");
    vi.resetModules();
    await expect(import("../next.config")).rejects.toThrow(/UPLOAD_BUCKET_HOST/);
  });
});
