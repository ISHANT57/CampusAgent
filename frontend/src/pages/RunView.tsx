import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowUp, Ban, Paperclip, Sparkles } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/api/client";
import { ApiError, type RunDetail } from "@/api/types";
import { SourceChipStrip } from "@/components/run/SourceChipStrip";
import { SourcesRail } from "@/components/run/SourcesRail";
import { StatusBadge } from "@/components/run/StatusBadge";
import { Timeline } from "@/components/run/Timeline";
import { Button } from "@/components/ui/Button";
import { useProvider } from "@/hooks/useProvider";
import { isTerminal, useRunStream } from "@/hooks/useRunStream";
import { extractSources } from "@/lib/sources";
import { cn, formatSeconds } from "@/lib/utils";

type Tab = "answer" | "reasoning";

/**
 * The question, then a choice: the clean synthesized answer, or the full
 * reasoning trace behind it.
 *
 * Defaults to the REASONING tab, not Answer. A search engine defaults to the
 * answer because that is what its users want; this is a developer tool built
 * on the opposite premise — the trace is the thing being inspected, and the
 * answer tab is a genuinely useful reading mode, not a replacement for that.
 */
export function RunView() {
  const { id } = useParams();
  const runId = id ? Number(id) : null;
  const navigate = useNavigate();
  const stream = useRunStream(runId);
  const { config, hasProvider } = useProvider();
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [tab, setTab] = useState<Tab>("reasoning");
  const [activeSource, setActiveSource] = useState<number | null>(null);
  const [followUp, setFollowUp] = useState("");
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);

  // Once the stream closes, load the full record: the stream carries
  // summaries, so totals, complete payloads, and the structured per-source
  // data the Sources rail needs all come from the run endpoint.
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
  const sources = detail ? extractSources(detail.steps) : [];
  const model = detail?.model ?? stream.model;

  async function cancel() {
    if (runId == null) return;
    setCancelling(true);
    try {
      await api.cancelRun(runId);
    } finally {
      setCancelling(false);
    }
  }

  function selectSource(index: number) {
    setActiveSource(index);
    document.getElementById(`source-card-${index}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function askFollowUp() {
    const value = followUp.trim();
    if (!value || asking || !hasProvider) return;
    setAsking(true);
    setAskError(null);
    try {
      const run = await api.createRun(value, config);
      navigate(`/runs/${run.run_id}`);
    } catch (e) {
      const err = e as ApiError;
      setAskError(
        err.reason === "hosted_unconfigured" || err.reason === "missing_key"
          ? "Connect an AI provider to run the agent."
          : err.status === 429
            ? "Going a bit fast — wait a moment and try again."
            : err.message,
      );
      setAsking(false);
    }
  }

  return (
    <div className="flex flex-col lg:flex-row">
      <div className="mx-auto w-full max-w-[760px] px-6 py-10">
        <div className="mb-3 flex items-center gap-1.5 font-heading text-xs font-semibold uppercase tracking-wider text-[var(--color-accent)]">
          <Sparkles size={12} /> Answer
        </div>

        <h1 className="font-heading text-[1.6rem] font-bold leading-snug tracking-tight text-[var(--color-text)]">
          {stream.goal ?? detail?.goal ?? "…"}
        </h1>

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-[var(--color-faint)]">
          <StatusBadge status={stream.status} />
          <span className="mono">{model ?? ""}</span>
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

        <div className="mt-6 flex items-center gap-5 border-b border-[var(--color-border)]">
          <TabButton active={tab === "answer"} onClick={() => setTab("answer")}>
            Answer
          </TabButton>
          <TabButton active={tab === "reasoning"} onClick={() => setTab("reasoning")}>
            Reasoning{toolCount > 0 && ` · ${toolCount}`}
          </TabButton>
        </div>

        {tab === "reasoning" ? (
          <div className="pt-6">
            <Timeline steps={stream.steps} running={running} idleSeconds={stream.idleSeconds} />
          </div>
        ) : (
          <div className="pt-6">
            <SourceChipStrip sources={sources} onSelect={selectSource} />

            {answer ? (
              <div className="prose-answer">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{answer}</ReactMarkdown>
              </div>
            ) : running ? (
              <div className="animate-breathe text-sm text-[var(--color-muted)]">
                thinking… <span className="mono">{stream.idleSeconds.toFixed(1)}s</span>
              </div>
            ) : null}

            {error && (
              <div className="mt-4 rounded-xl border border-[var(--color-bad)]/30 bg-[var(--color-bad)]/5 px-4 py-3 text-sm text-[var(--color-bad)]">
                {error}
              </div>
            )}

            {/* On lg the rail runs alongside; below it, sources stack here. */}
            <SourcesRail
              sources={sources}
              activeIndex={activeSource}
              className="mt-10 border-t border-[var(--color-border)] pt-8 lg:hidden"
            />
          </div>
        )}

        <div className="mt-10 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3 shadow-sm shadow-black/5">
          <textarea
            value={followUp}
            onChange={(e) => setFollowUp(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void askFollowUp();
              }
            }}
            rows={1}
            placeholder="Ask a follow-up…"
            className="w-full resize-none bg-transparent px-1 py-1.5 text-sm outline-none placeholder:text-[var(--color-faint)]"
          />
          <div className="mt-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <button
                disabled
                title="Attachments aren't supported yet"
                aria-label="Attach a file (not yet supported)"
                className="flex h-8 w-8 items-center justify-center rounded-lg text-[var(--color-faint)] disabled:cursor-not-allowed"
              >
                <Paperclip size={15} />
              </button>
              {model && (
                <span className="mono flex items-center gap-1.5 rounded-full border border-[var(--color-border)] px-2.5 py-1 text-xs text-[var(--color-muted)]">
                  <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-accent)]" />
                  {model}
                </span>
              )}
            </div>
            <Button
              onClick={() => void askFollowUp()}
              disabled={!followUp.trim() || asking || !hasProvider}
              className="h-11 w-11 rounded-full p-0"
              aria-label="Ask"
            >
              <ArrowUp size={15} />
            </Button>
          </div>
        </div>
        {askError && <p className="mt-2 text-sm text-[var(--color-bad)]">{askError}</p>}
      </div>

      {/* Only alongside the Answer tab — Reasoning already shows each
          knowledge_search/web_search result inline in its own step card, so a
          second rail repeating the same evidence would just be noise there. */}
      {tab === "answer" && (
        <SourcesRail
          sources={sources}
          activeIndex={activeSource}
          className="hidden shrink-0 border-l border-[var(--color-border)] px-6 py-10 lg:block lg:w-80"
        />
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "border-b-2 pb-2.5 text-sm font-medium transition-colors",
        active
          ? "border-[var(--color-accent)] text-[var(--color-text)]"
          : "border-transparent text-[var(--color-muted)] hover:text-[var(--color-text)]",
      )}
    >
      {children}
    </button>
  );
}

function Banner({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-4 rounded-xl border border-[var(--color-warn)]/30 bg-[var(--color-warn)]/5 px-4 py-2.5 text-sm text-[var(--color-warn)]">
      {children}
    </div>
  );
}
