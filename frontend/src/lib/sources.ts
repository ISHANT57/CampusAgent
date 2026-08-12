import type { StoredStep } from "@/api/types";

/** A numbered citation, shown in the source-chip strip and the Sources rail.
 *
 *  Built only from web_search and knowledge_search observations — a
 *  calculator result has no title, domain, or snippet to show as a source,
 *  and fabricating one would be worse than omitting it. Numbering runs
 *  sequentially across the whole run in step order, not restarted per tool
 *  call, so the same number always means the same source everywhere it
 *  appears. */
export interface Source {
  index: number;
  kind: "web" | "knowledge";
  title: string;
  snippet: string;
  domain?: string;
  url?: string;
  documentId?: number;
  pageNumber?: number;
}

function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Requires the STORED steps (GET /runs/{id}), not the live SSE summary —
 *  the stream only carries a 600-char truncated preview string, not the
 *  structured per-result list this needs. So the rail can only be fully
 *  populated once a run finishes and the full record loads, same as the
 *  existing token/step totals on the run header. */
export function extractSources(steps: StoredStep[]): Source[] {
  const sources: Source[] = [];

  for (const step of steps) {
    if (step.kind !== "observation") continue;
    if (step.tool_name !== "web_search" && step.tool_name !== "knowledge_search") continue;

    const output = step.output as { ok?: boolean; data?: unknown } | null;
    if (!output?.ok || !Array.isArray(output.data)) continue;

    for (const item of output.data as Record<string, unknown>[]) {
      if (step.tool_name === "web_search") {
        const url = typeof item.url === "string" ? item.url : "";
        sources.push({
          index: sources.length + 1,
          kind: "web",
          title: String(item.title || url || "(untitled)"),
          snippet: String(item.content ?? ""),
          domain: url ? domainOf(url) : undefined,
          url: url || undefined,
        });
      } else {
        sources.push({
          index: sources.length + 1,
          kind: "knowledge",
          title: `Document ${item.document_id ?? "?"}`,
          snippet: String(item.text ?? ""),
          documentId: typeof item.document_id === "number" ? item.document_id : undefined,
          pageNumber: typeof item.page_number === "number" ? item.page_number : undefined,
        });
      }
    }
  }

  return sources;
}
