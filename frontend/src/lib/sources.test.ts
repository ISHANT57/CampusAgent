import { describe, expect, it } from "vitest";
import { extractSources } from "./sources";
import type { StoredStep } from "@/api/types";

function step(overrides: Partial<StoredStep>): StoredStep {
  return { idx: 0, kind: "observation", tool_name: null, output: null, error: null, ...overrides };
}

describe("extractSources", () => {
  it("returns nothing for a run with no web/knowledge observations", () => {
    const steps = [
      step({ idx: 0, kind: "tool_call", tool_name: "calculator" }),
      step({ idx: 1, kind: "observation", tool_name: "calculator", output: { ok: true, data: 4.3 } }),
    ];
    expect(extractSources(steps)).toEqual([]);
  });

  it("skips an unavailable or failed observation — nothing to cite", () => {
    const steps = [
      step({ idx: 0, kind: "observation", tool_name: "web_search", output: { ok: false, unavailable: true } }),
    ];
    expect(extractSources(steps)).toEqual([]);
  });

  it("maps a web_search result to a web source with a derived domain", () => {
    const steps = [
      step({
        idx: 0,
        kind: "observation",
        tool_name: "web_search",
        output: {
          ok: true,
          data: [{ title: "Sitare University", url: "https://www.sitare.org/scholarships", content: "..." }],
        },
      }),
    ];
    const [source] = extractSources(steps);
    expect(source).toMatchObject({
      index: 1,
      kind: "web",
      title: "Sitare University",
      domain: "sitare.org",       // www. stripped
      url: "https://www.sitare.org/scholarships",
    });
  });

  it("maps a knowledge_search result to a knowledge source with no domain", () => {
    const steps = [
      step({
        idx: 0,
        kind: "observation",
        tool_name: "knowledge_search",
        output: {
          ok: true,
          data: [{ text: "Minimum CGPA is 7.5.", document_id: 3, page_number: 12, score: 0.9, chunk_id: 5 }],
        },
      }),
    ];
    const [source] = extractSources(steps);
    expect(source).toMatchObject({
      index: 1,
      kind: "knowledge",
      title: "Document 3",
      documentId: 3,
      pageNumber: 12,
    });
    expect(source.domain).toBeUndefined();
  });

  it("numbers sequentially across multiple tool calls, not restarted per call", () => {
    const steps = [
      step({
        idx: 0, kind: "observation", tool_name: "knowledge_search",
        output: { ok: true, data: [{ text: "a", document_id: 1, page_number: 1 }, { text: "b", document_id: 1, page_number: 2 }] },
      }),
      step({
        idx: 1, kind: "observation", tool_name: "web_search",
        output: { ok: true, data: [{ title: "t", url: "https://x.test", content: "c" }] },
      }),
    ];
    const indexes = extractSources(steps).map((s) => s.index);
    expect(indexes).toEqual([1, 2, 3]);
  });

  it("does not crash on a malformed item and just stringifies what it can", () => {
    const steps = [
      step({ idx: 0, kind: "observation", tool_name: "web_search", output: { ok: true, data: [{}] } }),
    ];
    const [source] = extractSources(steps);
    expect(source.title).toBe("(untitled)");
    expect(source.url).toBeUndefined();
  });
});
