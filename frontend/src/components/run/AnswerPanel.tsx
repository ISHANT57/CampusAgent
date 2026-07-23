import { Check, Copy } from "lucide-react";
import { useState } from "react";

/**
 * The answer.
 *
 * Shown even when the run FAILED: the backend deliberately returns a
 * best-effort answer on budget exhaustion or a provider outage, and discarding
 * it in the UI would throw away work the agent actually did.
 */
export function AnswerPanel({ answer, error }: { answer: string | null; error: string | null }) {
  const [copied, setCopied] = useState(false);
  if (!answer && !error) return null;

  return (
    <div className="mt-6 space-y-3">
      {error && (
        <div className="rounded-lg border border-[var(--color-bad)]/40 bg-[var(--color-bad)]/5 px-4 py-3 text-sm text-[var(--color-bad)]">
          {error}
        </div>
      )}
      {answer && (
        <div className="rounded-lg border border-[var(--color-ok)]/30 bg-[var(--color-surface)] p-4">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-wider text-[var(--color-faint)]">
              {error ? "partial answer" : "answer"}
            </span>
            <button
              onClick={() => {
                navigator.clipboard.writeText(answer);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
              className="flex items-center gap-1 text-xs text-[var(--color-faint)] hover:text-[var(--color-text)]"
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
              {copied ? "copied" : "copy"}
            </button>
          </div>
          <p className="whitespace-pre-wrap leading-relaxed">{answer}</p>
        </div>
      )}
    </div>
  );
}
