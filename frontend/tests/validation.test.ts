import { describe, expect, it } from "vitest";
import { validateGithubUrl } from "@/lib/validation";
import { sanitizeSvg } from "@/lib/sanitize";

describe("validateGithubUrl", () => {
  it("normalises a schemeless github.com url", () => {
    const r = validateGithubUrl("github.com/vercel/next.js");
    expect(r).toMatchObject({ ok: true, value: { owner: "vercel", repo: "next.js", url: "https://github.com/vercel/next.js" } });
  });

  it("rejects non-https, other hosts, credentials and bare owners", () => {
    for (const bad of ["http://github.com/a/b", "https://gitlab.com/a/b", "https://u:p@github.com/a/b", "https://github.com/a"]) {
      expect(validateGithubUrl(bad).ok).toBe(false);
    }
  });
});

describe("sanitizeSvg", () => {
  it("strips scripts and event handlers", async () => {
    const out = await sanitizeSvg('<svg onload="x()"><script>alert(1)</script><rect width="1" height="1"/></svg>');
    expect(out).not.toContain("script");
    expect(out).not.toContain("onload");
    expect(out).toContain("rect");
  });
});
