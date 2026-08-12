import { useState } from "react";
import { FileText } from "lucide-react";
import type { Source } from "@/lib/sources";
import { cn } from "@/lib/utils";

const INITIAL_LIMIT = 5;

/** Right rail below lg it stacks below the answer instead — the caller
 *  controls that via the className it passes in, not this component, so the
 *  same list renders either way without a second implementation. */
export function SourcesRail({
  sources,
  activeIndex,
  className,
}: {
  sources: Source[];
  activeIndex: number | null;
  className?: string;
}) {
  const [showAll, setShowAll] = useState(false);
  if (sources.length === 0) return null;

  const shown = showAll ? sources : sources.slice(0, INITIAL_LIMIT);

  return (
    <div className={className}>
      <div className="mb-3 font-heading text-sm font-semibold text-[var(--color-text)]">
        Sources {sources.length}
      </div>
      <div className="space-y-2">
        {shown.map((source) => (
          <SourceCard key={source.index} source={source} active={source.index === activeIndex} />
        ))}
      </div>
      {!showAll && sources.length > INITIAL_LIMIT && (
        <button
          onClick={() => setShowAll(true)}
          className="mt-3 w-full rounded-lg border border-[var(--color-border)] py-2 text-xs font-medium text-[var(--color-muted)] transition-colors hover:border-[var(--color-border-strong)] hover:text-[var(--color-text)]"
        >
          Show all {sources.length} sources
        </button>
      )}
    </div>
  );
}

function SourceCard({ source, active }: { source: Source; active: boolean }) {
  const meta =
    source.kind === "web"
      ? source.domain
      : source.pageNumber != null
        ? `page ${source.pageNumber}`
        : undefined;

  return (
    <a
      id={`source-card-${source.index}`}
      href={source.url}
      target={source.url ? "_blank" : undefined}
      rel={source.url ? "noreferrer" : undefined}
      className={cn(
        "block rounded-2xl border p-3 shadow-sm shadow-black/5 transition-colors",
        active
          ? "border-[var(--color-accent-tint-border)] bg-[var(--color-accent-tint)]"
          : "border-[var(--color-border)] bg-[var(--color-surface)] hover:border-[var(--color-border-strong)]",
        !source.url && "cursor-default",
      )}
    >
      <div className="flex items-start gap-2.5">
        <Glyph source={source} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-[var(--color-text)]">{source.title}</div>
          {meta && <div className="mono truncate text-xs text-[var(--color-muted)]">{meta}</div>}
        </div>
        <span
          className={cn(
            "mono flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-medium",
            active
              ? "bg-[var(--color-accent)] text-white"
              : "bg-[var(--color-surface-2)] text-[var(--color-muted)]",
          )}
        >
          {source.index}
        </span>
      </div>
      <p className="mt-1.5 line-clamp-2 text-xs leading-relaxed text-[var(--color-muted)]">
        {source.snippet}
      </p>
    </a>
  );
}

function Glyph({ source }: { source: Source }) {
  if (source.kind === "knowledge") {
    return (
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-[var(--color-surface-2)] text-[var(--color-muted)]">
        <FileText size={13} />
      </span>
    );
  }
  return (
    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-[var(--color-surface-2)] text-xs font-semibold uppercase text-[var(--color-muted)]">
      {(source.domain ?? "?").charAt(0)}
    </span>
  );
}
