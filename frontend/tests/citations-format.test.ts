import { describe, expect, it } from "vitest";
import {
  appendixItems,
  citationHref,
  citationSource,
  citedLabels,
  compactLocation,
  splitNotConfirmed,
} from "@/lib/citations";
import type { EvidenceRef, SectionContent } from "@/lib/api/types";

const SHA = "b122e9de3435f4d98c2b5616f20b95ce8bdfa0bd";
const ref = (over: Partial<EvidenceRef> = {}): EvidenceRef => ({
  chunk_id: "c1",
  path: "app/services/retriever.py",
  start_line: 12,
  end_line: 40,
  symbol: null,
  score: null,
  ...over,
});

describe("compactLocation", () => {
  it("formats a range, a single line and a bare file", () => {
    expect(compactLocation(ref())).toBe("app/services/retriever.py:L12-L40");
    expect(compactLocation(ref({ start_line: 7, end_line: 7 }))).toBe("app/services/retriever.py:L7");
    expect(compactLocation(ref({ start_line: 0, end_line: 0 }))).toBe("app/services/retriever.py");
  });

  it("keeps the trailing segments and the line range of a long path", () => {
    const long = ref({ path: "backend/src/unfold/orchestration/adapters/very/deep/pipeline_stage_runner.py" });
    const out = compactLocation(long, 40);
    expect(out.startsWith("…/")).toBe(true);
    expect(out.endsWith("pipeline_stage_runner.py:L12-L40")).toBe(true);
    expect(out.replace(/:L12-L40$/, "").length).toBeLessThanOrEqual(40);
    const huge = compactLocation(ref({ path: `a/${"x".repeat(80)}.py` }), 40);
    expect(huge.endsWith(".py:L12-L40") && huge.startsWith("…")).toBe(true);
  });
});

describe("citationHref", () => {
  const gh = citationSource({
    input_type: "github",
    source_locator: "https://github.com/psf/requests/tree/main",
    commit_sha: SHA,
  });

  it("pins a GitHub project to the analysed commit with a line anchor", () => {
    expect(citationHref(ref(), gh)).toBe(
      `https://github.com/psf/requests/blob/${SHA}/app/services/retriever.py#L12-L40`,
    );
    expect(citationHref(ref({ start_line: 5, end_line: 5 }), gh)).toMatch(/#L5$/);
    expect(citationHref(ref({ start_line: 0, end_line: 0 }), gh)).not.toContain("#");
  });

  it("encodes path segments", () => {
    expect(citationHref(ref({ path: "docs/a b/#x.md" }), gh)).toContain("/docs/a%20b/%23x.md#");
  });

  it("is not a link without a usable SHA, for ZIP uploads or without a path", () => {
    const noSha = citationSource({ input_type: "github", source_locator: "github.com/psf/requests", commit_sha: null });
    expect(noSha.repoUrl).toBe("https://github.com/psf/requests");
    expect(citationHref(ref(), noSha)).toBeNull();
    const badSha = citationSource({ input_type: "github", source_locator: "github.com/a/b", commit_sha: "not a sha" });
    expect(badSha.sha).toBeNull();
    const zip = citationSource({ input_type: "zip", source_locator: "repo.zip", commit_sha: SHA });
    expect(zip).toEqual({ repoUrl: null, sha: null });
    expect(citationHref(ref(), zip)).toBeNull();
    expect(citationHref(ref({ path: "" }), gh)).toBeNull();
    expect(citationHref(ref(), undefined)).toBeNull();
  });

  it("does not pin a locator that is not a GitHub repository URL", () => {
    const other = citationSource({ input_type: "github", source_locator: "https://evil.example/a/b", commit_sha: SHA });
    expect(other.repoUrl).toBeNull();
  });
});

describe("unconfirmed statements and the appendix", () => {
  it("detects the prefix", () => {
    expect(splitNotConfirmed("Not confirmed in the code: it retries.")).toEqual({
      unconfirmed: true,
      text: "it retries.",
    });
    expect(splitNotConfirmed("It retries.")).toEqual({ unconfirmed: false, text: "It retries." });
  });

  const content: SectionContent = {
    summary: "s",
    blocks: [
      { type: "paragraph", text: "A [E2] and B [E1, E3]." },
      { type: "bullets", items: ["again [E2]", "none"], ordered: false },
      { type: "qa", question: "q", answer: "see [E4]", kind: "repository" },
    ],
  };

  it("lists each cited label once, in order of use", () => {
    expect(citedLabels(content)).toEqual(["E2", "E1", "E3", "E4"]);
  });

  it("builds a de-duplicated appendix sorted by location and skips unresolved labels", () => {
    const evidence: Record<string, EvidenceRef> = {
      E1: ref({ path: "b.py", start_line: 1, end_line: 2 }),
      E2: ref({ path: "a.py", start_line: 9, end_line: 9 }),
      E3: ref({ path: "a.py", start_line: 9, end_line: 9, chunk_id: "dup" }),
    };
    const items = appendixItems(content, evidence);
    expect(items.map((i) => compactLocation(i.ref))).toEqual(["a.py:L9", "b.py:L1-L2"]);
  });
});
