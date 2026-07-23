import { useState } from "react";
import { AlertTriangle, ChevronRight } from "lucide-react";
import type { StreamStep } from "@/api/types";
import { cn, formatMs } from "@/lib/utils";

/** Icon per tool. Recognising a step by shape before reading it is most of
 *  what makes a long trace scannable. */
function toolIcon(tool: string | null) {
  if (!tool) return "•";
  if (tool.startsWith("knowledge_")) return "🔍";
  if (tool.startsWith("web_")) return "🌐";
  if (tool === "calculator") return "🧮";
  return "⚙";
}

interface Props {
  step: StreamStep;
  /** The paired observation, when this is a tool_call.
   *
   *  The backend records tool_call and observation as separate rows — correctly,
   *  they happen at different times and one may never arrive. But a human reads
   *  "it searched and got 5 results" as ONE action, so the UI pairs them. */
  observation?: StreamStep;
  /** No observation yet: this call is still in flight. */
  pending?: boolean;
  elapsed?: number;
}

export function StepCard({ step, observation, pending, elapsed }: Props) {
  const [expanded, setExpanded] = useState(false);

  const failed = observation?.ok === false;
  const unavailable = observation?.unavailable === true;

  return (
    <div
      className={cn(
        "rounded-lg border bg-[var(--color-surface)] transition-colors",
        pending
          ? "border-[var(--color-accent)]/40"
          : unavailable
            ? "border-[var(--color-warn)]/40"
            : failed
              ? "border-[var(--color-bad)]/40"
              : "border-[var(--color-border)]",
      )}
    >
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-start gap-3 px-4 py-3 text-left"
      >
        <span className="mt-0.5 text-base leading-none">{toolIcon(step.tool)}</span>

        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="font-medium text-[var(--color-tool)]">{step.tool}</span>
            {/* The key argument inline: a trace where every card must be
                opened to see what was asked is a list, not a trace. */}
            <span className="truncate text-sm text-[var(--color-muted)] mono">
              {primaryArgument(step)}
            </span>
          </div>

          {observation && !expanded && (
            <div className="mt-1 truncate text-xs text-[var(--color-faint)]">
              {observation.preview?.slice(0, 120)}
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2 text-xs">
          {pending ? (
            // The live counter. This is the whole answer to "is it stuck?" —
            // a spinner says wait without saying what for or how long.
            <span className="animate-breathe text-[var(--color-accent)] mono">
              {elapsed?.toFixed(1)}s
            </span>
          ) : (
            <>
              <StatusPip ok={observation?.ok} unavailable={unavailable} />
              {observation?.count != null && (
                <span className="text-[var(--color-muted)]">{observation.count}</span>
              )}
              <span className="text-[var(--color-faint)] mono">
                {formatMs(observation?.latency_ms)}
              </span>
            </>
          )}
          <ChevronRight
            size={14}
            className={cn(
              "text-[var(--color-faint)] transition-transform",
              expanded && "rotate-90",
            )}
          />
        </div>
      </button>

      {expanded && (
        <div className="border-t border-[var(--color-border)] px-4 py-3 text-sm">
          <Labelled label="arguments">
            <pre className="mono overflow-x-auto text-xs text-[var(--color-muted)]">
              {JSON.stringify(step.arguments ?? {}, null, 2)}
            </pre>
          </Labelled>

          {observation && (
            <Labelled label={unavailable ? "unavailable" : failed ? "failed" : "result"}>
              <pre className="mono max-h-80 overflow-auto whitespace-pre-wrap text-xs text-[var(--color-muted)]">
                {observation.error ?? observation.preview ?? "(no content)"}
              </pre>
              {observation.truncated && (
                // Announced, never silent: an agent — or a reader — that does
                // not know it saw a partial result will draw a confident wrong
                // conclusion from it.
                <p className="mt-2 text-xs text-[var(--color-faint)]">
                  Truncated for the live stream. Open the run again to load the full record.
                </p>
              )}
            </Labelled>
          )}
        </div>
      )}
    </div>
  );
}

function StatusPip({ ok, unavailable }: { ok?: boolean; unavailable?: boolean }) {
  // Three states, three colours. This is the distinction the whole backend is
  // built around: "the service is down" must never look like "the corpus has
  // no answer".
  if (unavailable)
    return (
      <span className="flex items-center gap-1 text-[var(--color-warn)]">
        <AlertTriangle size={12} /> unavailable
      </span>
    );
  if (ok === false) return <span className="text-[var(--color-bad)]">✗ failed</span>;
  return <span className="text-[var(--color-ok)]">✓</span>;
}

function Labelled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-3 last:mb-0">
      <div className="mb-1 text-[10px] uppercase tracking-wider text-[var(--color-faint)]">
        {label}
      </div>
      {children}
    </div>
  );
}

function primaryArgument(step: StreamStep): string {
  const args = step.arguments ?? {};
  // Show the argument a human would name the step by, not the first key.
  for (const key of ["query", "expression", "url", "document_id", "status"]) {
    if (key in args) return String(args[key]);
  }
  const entries = Object.entries(args);
  return entries.length ? `${entries[0][0]}: ${entries[0][1]}` : "";
}
