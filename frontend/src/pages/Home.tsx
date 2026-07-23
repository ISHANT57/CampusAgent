import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowUp, KeyRound, Sparkles } from "lucide-react";
import { api } from "@/api/client";
import { ApiError } from "@/api/types";
import { Button } from "@/components/ui/Button";
import { useProvider } from "@/hooks/useProvider";

const EXAMPLES = [
  {
    text: "My CGPA is 6.2. Look up the minimum Sitare requires for a scholarship and calculate how far short I am.",
    tools: "knowledge + calculator",
  },
  {
    text: "Summarise all of the hostel rules — the complete list, not just highlights.",
    tools: "whole document",
  },
  {
    text: "Find the latest AI/ML internship openings at Indian startups.",
    tools: "web search",
  },
  {
    text: "Compare the hostel timings in our documents against Sitare's website.",
    tools: "two sources",
  },
];

/** The empty state. Centred and generous, in the Perplexity mould: one input,
 *  large, with nothing competing for attention until there is something to
 *  show. */
export function Home() {
  const navigate = useNavigate();
  const { config, hasProvider } = useProvider();
  const [goal, setGoal] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(text?: string) {
    const value = (text ?? goal).trim();
    if (!value || submitting || !hasProvider) return;
    setSubmitting(true);
    setError(null);
    try {
      const run = await api.createRun(value, config);
      navigate(`/runs/${run.run_id}`);
    } catch (e) {
      const err = e as ApiError;
      // The backend's typed reasons deserve different messages: "connect a
      // provider" is a next step, "something went wrong" is a dead end.
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
    <div className="flex min-h-full flex-col items-center justify-center px-4 py-16">
      <div className="w-full max-w-2xl">
        <div className="mb-10 text-center">
          <h1 className="text-[2rem] font-semibold tracking-tight">
            What should the agent do?
          </h1>
          <p className="mt-2 text-[15px] text-[var(--color-muted)]">
            Give it a goal. It picks its own tools and shows you every step.
          </p>
        </div>

        {!hasProvider && (
          <Link
            to="/settings"
            className="mb-4 flex items-center gap-3 rounded-xl border border-[var(--color-accent)]/30 bg-[var(--color-accent)]/5 px-4 py-3 text-sm transition-colors hover:bg-[var(--color-accent)]/10"
          >
            <KeyRound size={16} className="text-[var(--color-accent)]" />
            <span className="flex-1">
              <span className="font-medium">Connect an AI provider</span>
              <span className="text-[var(--color-muted)]">
                {" "}
                — bring your own key, no limits. Ollama needs no key at all.
              </span>
            </span>
          </Link>
        )}

        <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-2 shadow-lg shadow-black/20 transition-colors focus-within:border-[var(--color-border-strong)]">
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            onKeyDown={(e) => {
              // Enter submits; Shift+Enter is a newline. The familiar contract.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit();
              }
            }}
            rows={3}
            autoFocus
            placeholder="Ask anything about Sitare, or give it a multi-step task…"
            className="w-full resize-none bg-transparent px-3 py-2.5 text-[15px] leading-relaxed outline-none placeholder:text-[var(--color-faint)]"
          />
          <div className="flex items-center justify-between px-3 pb-1">
            <span className="truncate text-xs text-[var(--color-faint)] mono">
              {config ? `${config.provider} · ${config.model ?? "default"}` : "no provider"}
            </span>
            <Button
              onClick={() => submit()}
              disabled={!goal.trim() || submitting || !hasProvider}
              className="h-8 w-8 rounded-full p-0"
              aria-label="Run"
            >
              <ArrowUp size={16} />
            </Button>
          </div>
        </div>

        {error && <p className="mt-3 text-center text-sm text-[var(--color-bad)]">{error}</p>}

        <div className="mt-8 space-y-2">
          {EXAMPLES.map((example) => (
            <button
              key={example.text}
              onClick={() => submit(example.text)}
              disabled={!hasProvider || submitting}
              className="group flex w-full items-start gap-3 rounded-xl border border-transparent px-4 py-3 text-left transition-colors hover:border-[var(--color-border)] hover:bg-[var(--color-surface)] disabled:opacity-50"
            >
              <Sparkles
                size={15}
                className="mt-0.5 shrink-0 text-[var(--color-faint)] group-hover:text-[var(--color-accent)]"
              />
              <span className="flex-1 text-sm text-[var(--color-muted)] group-hover:text-[var(--color-text)]">
                {example.text}
              </span>
              <span className="shrink-0 pt-0.5 text-[10px] text-[var(--color-faint)]">
                {example.tools}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
