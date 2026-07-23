import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Ban } from "lucide-react";
import { api } from "@/api/client";
import type { RunDetail } from "@/api/types";
import { AnswerPanel } from "@/components/run/AnswerPanel";
import { StatusBadge } from "@/components/run/StatusBadge";
import { Timeline } from "@/components/run/Timeline";
import { Button } from "@/components/ui/Button";
import { isTerminal, useRunStream } from "@/hooks/useRunStream";
import { formatSeconds } from "@/lib/utils";

export function RunView() {
  const { id } = useParams();
  const runId = id ? Number(id) : null;
  const stream = useRunStream(runId);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [cancelling, setCancelling] = useState(false);

  // Once the stream closes, fetch the full record: the stream carries
  // summaries (a single observation is several kilobytes), so totals and the
  // complete payloads come from the run endpoint.
  useEffect(() => {
    if (runId == null || !isTerminal(stream.status)) return;
    api.getRun(runId).then(setDetail).catch(() => {});
  }, [runId, stream.status]);

  if (runId == null) return null;

  const running = stream.status === "running" || stream.status === "connecting";
  const degraded = stream.steps.some((s) => s.unavailable);
  const answer = stream.answer ?? detail?.answer ?? null;
  const error = stream.error ?? detail?.error ?? null;

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
    <div className="mx-auto max-w-3xl px-4 py-6">
      <div className="mb-5 flex items-center justify-between gap-4">
        <Link
          to="/"
          className="flex items-center gap-1.5 text-sm text-[var(--color-muted)] hover:text-[var(--color-text)]"
        >
          <ArrowLeft size={14} /> Runs
        </Link>

        <div className="flex items-center gap-4 text-xs text-[var(--color-faint)]">
          <StatusBadge status={stream.status} />
          {detail && (
            <span className="mono">
              {detail.step_count} steps · {detail.prompt_tokens + detail.completion_tokens} tok ·{" "}
              {formatSeconds(detail.elapsed_seconds)}
            </span>
          )}
          {running && (
            <Button variant="danger" onClick={cancel} disabled={cancelling} className="px-2 py-1 text-xs">
              <Ban size={12} />
              {/* Cancellation is cooperative — the loop checks at the top of
                  each iteration, so an in-flight provider call still finishes.
                  Saying "cancelling" is honest; jumping to "cancelled" is not. */}
              {cancelling ? "cancelling…" : "cancel"}
            </Button>
          )}
        </div>
      </div>

      <div className="mb-5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <div className="mb-1 text-[10px] uppercase tracking-wider text-[var(--color-faint)]">goal</div>
        <p className="leading-relaxed">{stream.goal ?? detail?.goal ?? "…"}</p>
        {(stream.model || detail?.model) && (
          <p className="mt-2 text-xs text-[var(--color-faint)] mono">
            {stream.provider ?? detail?.provider} · {stream.model ?? detail?.model}
          </p>
        )}
      </div>

      {stream.reconnecting && (
        // Not a failure: EventSource is retrying and will resend
        // Last-Event-ID, so the trace resumes where it left off.
        <Banner tone="warn">Connection lost — resuming…</Banner>
      )}

      {degraded && (
        // An outage and bad reasoning produce the same-looking result. Naming
        // it stops a web-search fallback reading as the agent ignoring the corpus.
        <Banner tone="warn">
          A tool was unavailable during this run — the answer may be incomplete.
        </Banner>
      )}

      <Timeline steps={stream.steps} running={running} idleSeconds={stream.idleSeconds} />

      <AnswerPanel answer={answer} error={error} />
    </div>
  );
}

function Banner({ tone, children }: { tone: "warn"; children: React.ReactNode }) {
  return (
    <div
      className={`mb-4 rounded-lg border px-4 py-2 text-sm ${
        tone === "warn"
          ? "border-[var(--color-warn)]/40 bg-[var(--color-warn)]/5 text-[var(--color-warn)]"
          : ""
      }`}
    >
      {children}
    </div>
  );
}
