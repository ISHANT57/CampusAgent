import type { StreamStep } from "@/api/types";
import { StepCard } from "./StepCard";

interface Props {
  steps: StreamStep[];
  running: boolean;
  idleSeconds: number;
}

/**
 * The vertical timeline.
 *
 * Deliberately not a node graph. Those tools draw graphs because their agents
 * ARE graphs; this one is a linear loop, and a DAG would imply branching the
 * runtime does not have.
 *
 * tool_call and observation are paired into one card here (see StepCard) —
 * two rows in the database, one action to a reader.
 */
export function Timeline({ steps, running, idleSeconds }: Props) {
  const observations = new Map<string, StreamStep>();
  for (const step of steps) {
    if (step.kind === "observation" && step.tool) {
      // Pair with the tool_call immediately before it. Keyed by index so two
      // calls to the same tool in one run do not collapse into each other.
      observations.set(`${step.tool}:${step.idx - 1}`, step);
    }
  }

  const rendered: JSX.Element[] = [];
  let number = 0;

  steps.forEach((step, i) => {
    if (step.kind === "observation") return; // drawn with its tool_call

    if (step.kind === "tool_call") {
      number += 1;
      const observation = observations.get(`${step.tool}:${step.idx}`);
      const isLast = i === steps.length - 1;
      rendered.push(
        <Row key={step.idx} number={number}>
          <StepCard
            step={step}
            observation={observation}
            // No observation yet AND nothing after it: this call is in flight.
            pending={!observation && isLast && running}
            elapsed={idleSeconds}
          />
        </Row>,
      );
      return;
    }

    if (step.kind === "thought") {
      const text = String(step.output?.text ?? "");
      if (!text.trim()) return;
      rendered.push(
        // Muted and unbordered: context, not an event. Styling thoughts like
        // actions drowns the actions.
        <Row key={step.idx}>
          <p className="px-1 py-1 text-sm italic leading-relaxed text-[var(--color-muted)]">
            {text}
          </p>
        </Row>,
      );
      return;
    }

    if (step.kind === "plan") {
      rendered.push(
        <Row key={step.idx} label="plan">
          <pre className="mono whitespace-pre-wrap rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-xs text-[var(--color-muted)]">
            {JSON.stringify(step.output, null, 2)}
          </pre>
        </Row>,
      );
      return;
    }

    if (step.kind === "reflection") {
      rendered.push(
        <Row key={step.idx} label="reflection">
          <div className="rounded-lg border-l-2 border-[var(--color-warn)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-muted)]">
            {String(step.output?.text ?? "")}
          </div>
        </Row>,
      );
      return;
    }

    if (step.kind === "error") {
      rendered.push(
        // Never truncated. Truncating the one thing you need to debug is the
        // worst possible cut.
        <Row key={step.idx}>
          <div className="rounded-lg border border-[var(--color-bad)]/40 bg-[var(--color-bad)]/5 px-4 py-3 text-sm text-[var(--color-bad)]">
            {step.error}
          </div>
        </Row>,
      );
    }
    // `final` is rendered by AnswerPanel, not inline.
  });

  if (running && !steps.some((s) => s.kind === "tool_call")) {
    rendered.push(
      <Row key="thinking">
        <div className="animate-breathe px-1 py-2 text-sm text-[var(--color-muted)]">
          thinking… <span className="mono">{idleSeconds.toFixed(1)}s</span>
        </div>
      </Row>,
    );
  }

  return <div className="space-y-2">{rendered}</div>;
}

function Row({
  number,
  label,
  children,
}: {
  number?: number;
  label?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-3">
      <div className="w-6 shrink-0 pt-3 text-right text-xs text-[var(--color-faint)] mono">
        {number ?? (label ? "" : "")}
      </div>
      <div className="min-w-0 flex-1">
        {label && (
          <div className="mb-1 text-[10px] uppercase tracking-wider text-[var(--color-faint)]">
            {label}
          </div>
        )}
        {children}
      </div>
    </div>
  );
}
