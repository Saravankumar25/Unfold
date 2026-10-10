import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { EvidenceAppendix } from "@/components/content/evidence-appendix";
import { InlineText, Statement } from "@/components/content/inline-text";
import type { EvidenceRef } from "@/lib/api/types";
import { citationSource, mapResolver } from "@/lib/citations";

const SHA = "b122e9de3435f4d98c2b5616f20b95ce8bdfa0bd";
const REF: EvidenceRef = {
  chunk_id: "c1",
  path: "app/main.py",
  start_line: 3,
  end_line: 9,
  symbol: "main",
  score: null,
};
const resolve = mapResolver({ E1: REF });
const GH = citationSource({ input_type: "github", source_locator: "github.com/o/r", commit_sha: SHA });
const ZIP = citationSource({ input_type: "zip", source_locator: "r.zip", commit_sha: null });

describe("citations in running text", () => {
  it("renders a commit-pinned link for a GitHub project and never the [E1] label", () => {
    render(
      <p>
        <InlineText text="Entry point [E1]." resolve={resolve} source={GH} onCite={vi.fn()} />
      </p>,
    );
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", `https://github.com/o/r/blob/${SHA}/app/main.py#L3-L9`);
    expect(link).toHaveTextContent("app/main.py:L3-L9");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(document.body.textContent).not.toMatch(/E1/);
  });

  it("renders a non-link label that opens the evidence for a ZIP project", async () => {
    const onCite = vi.fn();
    render(
      <p>
        <InlineText text="Entry point [E1]." resolve={resolve} source={ZIP} onCite={onCite} />
      </p>,
    );
    expect(screen.queryByRole("link")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /app\/main\.py/ }));
    expect(onCite).toHaveBeenCalledWith("E1", REF);
  });

  it("drops a label it cannot resolve instead of showing it", () => {
    const { container } = render(
      <p>
        <InlineText text="Unknown [E7] here." resolve={resolve} source={GH} />
      </p>,
    );
    expect(container.textContent).toBe("Unknown  here.");
  });

  it("marks statements the verifier could not confirm", () => {
    render(
      <p>
        <Statement text="Not confirmed in the code: it retries on failure." resolve={resolve} />
      </p>,
    );
    expect(screen.getByText("Not confirmed in the code")).toBeInTheDocument();
    expect(document.querySelectorAll("[data-unconfirmed]")).toHaveLength(1);
    expect(document.body.textContent).toContain("it retries on failure.");
    render(
      <p>
        <Statement text="It retries." resolve={resolve} />
      </p>,
    );
    expect(document.querySelectorAll("[data-unconfirmed]")).toHaveLength(1);
  });
});

describe("EvidenceAppendix", () => {
  const items = [{ label: "E1", ref: REF }];

  it("is a collapsed list of locations with a GitHub link and an in-app view", async () => {
    const onOpen = vi.fn();
    render(<EvidenceAppendix items={items} source={GH} onOpen={onOpen} />);
    const details = screen.getByTestId("evidence-appendix") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(screen.getByText(/Evidence · 1 location/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "GitHub" })).toHaveAttribute(
      "href",
      `https://github.com/o/r/blob/${SHA}/app/main.py#L3-L9`,
    );
    expect(document.body.textContent).not.toMatch(/\bE1\b/);
    await userEvent.click(screen.getByRole("button", { name: "app/main.py:L3-L9" }));
    expect(onOpen).toHaveBeenCalledWith(items[0]);
  });

  it("has no GitHub link for a ZIP project and renders nothing when empty", () => {
    const { container, rerender } = render(<EvidenceAppendix items={items} source={ZIP} onOpen={vi.fn()} />);
    expect(screen.queryByRole("link")).toBeNull();
    rerender(<EvidenceAppendix items={[]} source={ZIP} onOpen={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
