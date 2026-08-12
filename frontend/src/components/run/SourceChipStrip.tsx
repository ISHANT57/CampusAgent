import { FileText } from "lucide-react";
import type { Source } from "@/lib/sources";
import { cn } from "@/lib/utils";

const STRIP_LIMIT = 5;

/** A compact preview of the cited sources, above the answer. Clicking a chip
 *  jumps to and highlights the matching card in the Sources rail — a real
 *  interaction, not decoration, which is also why there is no inline citation
 *  number inside the answer prose itself: nothing here claims a specific
 *  sentence came from a specific source, only that the run consulted these. */
export function SourceChipStrip({
  sources,
  onSelect,
}: {
  sources: Source[];
  onSelect: (index: number) => void;
}) {
  if (sources.length === 0) return null;

  const shown = sources.slice(0, STRIP_LIMIT);
  const overflow = sources.length - shown.length;

  return (
    <div className="scrollbar-none -mx-1 mb-6 flex gap-2 overflow-x-auto px-1 pb-1">
      {shown.map((source) => (
        <button
          key={source.index}
          onClick={() => onSelect(source.index)}
          className="flex shrink-0 items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] py-1 pl-1 pr-2.5 text-xs transition-colors hover:border-[var(--color-border-strong)]"
        >
          <ChipGlyph source={source} />
          <span className="mono text-[var(--color-muted)]">
            {source.domain ?? `doc ${source.documentId ?? "?"}`}
          </span>
          <span className="mono text-[var(--color-accent)]">{source.index}</span>
        </button>
      ))}
      {overflow > 0 && (
        <span className="flex shrink-0 items-center rounded-full border border-[var(--color-border)] bg-[var(--color-surface-2)] px-2.5 py-1 text-xs mono text-[var(--color-muted)]">
          +{overflow}
        </span>
      )}
    </div>
  );
}

function ChipGlyph({ source }: { source: Source }) {
  if (source.kind === "knowledge") {
    return (
      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-sm bg-[var(--color-surface-2)] text-[var(--color-muted)]">
        <FileText size={10} />
      </span>
    );
  }
  return (
    <span
      className={cn(
        "flex h-4 w-4 shrink-0 items-center justify-center rounded-sm text-[9px] font-semibold uppercase",
        "bg-[var(--color-surface-2)] text-[var(--color-muted)]",
      )}
    >
      {(source.domain ?? "?").charAt(0)}
    </span>
  );
}
