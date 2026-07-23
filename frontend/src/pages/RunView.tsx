import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Ban, ChevronDown, Sparkles } from "lucide-react";
import { api } from "@/api/client";
import type { RunDetail } from "@/api/types";
import { StatusBadge } from "@/components/run/StatusBadge";
import { Timeline } from "@/components/run/Timeline";
import { Button } from "@/components/ui/Button";
import { isTerminal, useRunStream } from "@/hooks/useRunStream";
import { cn, formatSeconds } from "@/lib/utils";

/**
 * Perplexity's shape: the question, then the work, then the answer.
 *
 * The steps stay EXPANDED by default. Perplexity collapses them because its
 * users want the answer; this is a developer tool, and the reasoning is the
 * thing being inspected. The toggle exists for when it is not.
 */
export function RunView() {
  const { id } = useParams();
  const runId = id ? Number(id) : null;
  const stream = useRunStream(runId);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [showSteps, setShowSteps] = useState(true);

  // Once the stream closes, load the full record: the stream carries
  // summaries, so totals and complete payloads come from the run endpoint.
  useEffect(() => {
    if (runId == null || !isTerminal(stream.status)) return;
    api.getRun(runId).then(setDetail).catch(() => {});
  }, [runId, stream.status]);

  if (runId == null) return null;

  const running = stream.status === "running" || stream.status === "connecting";
  const degraded = stream.steps.some((s) => s.unavailable);
  const answer = stream.answer ?? detail?.answer ?? null;
  const error = stream.error ?? detail?.error ?? null;
  const toolCount = stream.steps.filter((s) => s.kind === "tool_call").length;

  async function cancel() {
    if (runId == null) return;
    setCancelling(true);
    try {
      await api.cancelRun(runId);
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      {/* The question, as a heading. This is what the page is about. */}
      <h1 className="text-[1.6rem] font-semibold leading-snug tracking-tight">
        {stream.goal ?? detail?.goal ?? "…"}
      </h1>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-[var(--color-faint)]">
        <StatusBadge status={stream.status} />
        <span className="mono">{stream.model ?? detail?.model ?? ""}</span>
        {detail && (
          <span className="mono">
            {detail.step_count} steps · {detail.prompt_tokens + detail.completion_tokens} tok ·{" "}
            {formatSeconds(detail.elapsed_seconds)}
          </span>
        )}
        {running && (
          <Button
            variant="danger"
            onClick={cancel}
            disabled={cancelling}
            className="ml-auto h-7 px-2 py-0 text-xs"
          >
            <Ban size={12} />
            {/* Cancellation is cooperative — the loop checks at the top of each
                iteration, so an in-flight provider call still finishes. Saying
                "cancelling" is honest; jumping to "cancelled" is not. */}
            {cancelling ? "cancelling…" : "cancel"}
          </Button>
        )}
      </div>

      {stream.reconnecting && (
        // Not a failure: EventSource retries with Last-Event-ID and the trace
        // resumes where it left off.
        <Banner>Connection lost — resuming…</Banner>
      )}
      {degraded && (
        // An outage and bad reasoning produce the same-looking answer. Naming
        // it stops a web-search fallback reading as the agent ignoring the corpus.
        <Banner>A tool was unavailable during this run — the answer may be incomplete.</Banner>
      )}

      {/* --- the work ----------------------------------------------------- */}
      <section className="mt-8">
        <button
          onClick={() => setShowSteps((v) => !v)}
          className="mb-3 flex items-center gap-2 text-xs uppercase tracking-wider text-[var(--color-faint)] transition-colors hover:text-[var(--color-muted)]"
        >
          <ChevronDown
            size={13}
            className={cn("transition-transform", !showSteps && "-rotate-90")}
          />
          Reasoning
          {toolCount > 0 && <span className="normal-case">· {toolCount} tool calls</span>}
        </button>

        {showSteps && (
          <Timeline steps={stream.steps} running={running} idleSeconds={stream.idleSeconds} />
        )}
      </section>

      {/* --- the answer --------------------------------------------------- */}
      {(answer || error) && (
        <section className="mt-10 border-t border-[var(--color-border)] pt-8">
          {error && (
            <div className="mb-4 rounded-xl border border-[var(--color-bad)]/30 bg-[var(--color-bad)]/5 px-4 py-3 text-sm text-[var(--color-bad)]">
              {error}
            </div>
          )}
          {answer && (
            <>
              <div className="mb-3 flex items-center gap-2 text-xs uppercase tracking-wider text-[var(--color-faint)]">
                <Sparkles size={13} className="text-[var(--color-accent)]" />
                {/* Shown even when the run FAILED: the backend deliberately
                    returns a best-effort answer, and discarding it would throw
                    away work the agent actually did. */}
                {error ? "partial answer" : "answer"}
              </div>
              <div className="whitespace-pre-wrap text-[15px] leading-[1.75] text-[var(--color-text)]">
                {answer}
              </div>
            </>
          )}
        </section>
      )}
    </div>
  );
}

function Banner({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-4 rounded-xl border border-[var(--color-warn)]/30 bg-[var(--color-warn)]/5 px-4 py-2.5 text-sm text-[var(--color-warn)]">
      {children}
    </div>
  );
}
