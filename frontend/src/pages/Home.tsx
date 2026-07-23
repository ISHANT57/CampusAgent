import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { KeyRound, Send } from "lucide-react";
import { api } from "@/api/client";
import { ApiError, type RunSummary } from "@/api/types";
import { StatusBadge } from "@/components/run/StatusBadge";
import { Button } from "@/components/ui/Button";
import { useProvider } from "@/hooks/useProvider";
import { formatSeconds, formatWhen } from "@/lib/utils";

const EXAMPLES = [
  "My CGPA is 6.2. Look up the minimum Sitare requires for a scholarship and calculate how far short I am.",
  "What is the minimum CGPA I need to keep my Sitare scholarship?",
  "Summarise all of the hostel rules — the complete list, not just highlights.",
  "Find the latest AI/ML internship openings at Indian startups.",
];

export function Home() {
  const navigate = useNavigate();
  const { config, hasProvider } = useProvider();
  const [goal, setGoal] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);

  useEffect(() => {
    api.listRuns(15).then((r) => setRuns(r.runs)).catch(() => {});
  }, []);

  async function submit() {
    if (!goal.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const run = await api.createRun(goal.trim(), config);
      navigate(`/runs/${run.run_id}`);
    } catch (e) {
      const err = e as ApiError;
      // The backend's typed reasons deserve different messages. "hosted
      // trial is not configured" means connect a provider, not "something
      // went wrong".
      if (err.reason === "hosted_unconfigured" || err.reason === "missing_key") {
        setError("Connect an AI provider to run the agent.");
      } else if (err.status === 429) {
        setError("Going a bit fast — wait a moment and try again.");
      } else {
        setError(err.message);
      }
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold">CampusBrain Agent</h1>
        <p className="mt-1 text-sm text-[var(--color-muted)]">
          Give it a goal. It chooses its own tools, and shows you every step.
        </p>
      </header>

      {!hasProvider && (
        <div className="mb-6 flex items-start gap-3 rounded-lg border border-[var(--color-accent)]/30 bg-[var(--color-surface)] p-4">
          <KeyRound size={16} className="mt-0.5 text-[var(--color-accent)]" />
          <div className="flex-1 text-sm">
            <p className="font-medium">Connect an AI provider</p>
            <p className="mt-1 text-[var(--color-muted)]">
              You bring your own key, so there are no limits.{" "}
              <span className="text-[var(--color-text)]">Ollama needs no key at all.</span>
            </p>
          </div>
          <Link to="/settings">
            <Button className="px-3 py-1.5 text-xs">Connect</Button>
          </Link>
        </div>
      )}

      <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <textarea
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
          rows={3}
          placeholder="What should the agent accomplish?"
          className="w-full resize-none bg-transparent text-[15px] leading-relaxed outline-none placeholder:text-[var(--color-faint)]"
        />
        <div className="mt-3 flex items-center justify-between">
          <span className="text-xs text-[var(--color-faint)]">
            {config ? `${config.provider} · ${config.model ?? "default"}` : "no provider"}
          </span>
          <Button onClick={submit} disabled={!goal.trim() || submitting || !hasProvider}>
            <Send size={14} />
            {submitting ? "starting…" : "Run"}
          </Button>
        </div>
      </div>

      {error && <p className="mt-3 text-sm text-[var(--color-bad)]">{error}</p>}

      {!goal && (
        <div className="mt-6 space-y-2">
          {EXAMPLES.map((example) => (
            <button
              key={example}
              onClick={() => setGoal(example)}
              className="block w-full rounded-lg border border-[var(--color-border)] px-3 py-2 text-left text-sm text-[var(--color-muted)] hover:border-[var(--color-border-strong)] hover:text-[var(--color-text)]"
            >
              {example}
            </button>
          ))}
        </div>
      )}

      {runs.length > 0 && (
        <section className="mt-10">
          <h2 className="mb-3 text-xs uppercase tracking-wider text-[var(--color-faint)]">
            recent runs
          </h2>
          <div className="space-y-1">
            {runs.map((run) => (
              <Link
                key={run.run_id}
                to={`/runs/${run.run_id}`}
                className="flex items-center gap-3 rounded-lg px-3 py-2 hover:bg-[var(--color-surface)]"
              >
                <StatusBadge status={run.status} />
                <span className="min-w-0 flex-1 truncate text-sm">{run.goal}</span>
                <span className="shrink-0 text-xs text-[var(--color-faint)] mono">
                  {run.step_count} · {formatSeconds(run.elapsed_seconds)} ·{" "}
                  {formatWhen(run.created_at)}
                </span>
              </Link>
            ))}
          </div>
          {/* Runs belong to a browser cookie. Saying so beats letting someone
              discover it after clearing site data. */}
          <p className="mt-3 text-xs text-[var(--color-faint)]">
            History is tied to this browser. Clearing site data removes it — there are no
            accounts yet.
          </p>
        </section>
      )}
    </div>
  );
}
