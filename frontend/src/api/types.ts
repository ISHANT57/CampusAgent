// Mirrors the backend response shapes. Kept hand-written rather than generated
// so a backend change that breaks the UI shows up as a TypeScript error here,
// at the boundary, instead of as undefined deep inside a component.

export type RunStatus =
  | "created"
  | "planning"
  | "running"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "rejected"
  | "cancelled"
  | "timed_out";

export const TERMINAL: RunStatus[] = [
  "completed",
  "failed",
  "rejected",
  "cancelled",
  "timed_out",
];

/** Step kinds the loop writes. `plan` and `reflection` only appear once
 *  planning lands (M26) — the UI renders them already so that milestone needs
 *  no frontend change. */
export type StepKind =
  | "plan"
  | "thought"
  | "tool_call"
  | "observation"
  | "reflection"
  | "final"
  | "error";

/** A step as it arrives over SSE.
 *
 *  Deliberately a SUMMARY, not the stored row: one knowledge_search
 *  observation is several kilobytes, and the timeline draws one line from it.
 *  `preview` is capped at 600 chars server-side; the full payload comes from
 *  GET /runs/{id} when a card is expanded to "raw". */
export interface StreamStep {
  idx: number;
  kind: StepKind;
  tool: string | null;
  error: string | null;
  // observation
  ok?: boolean;
  unavailable?: boolean;
  count?: number | null;
  latency_ms?: number | null;
  preview?: string;
  truncated?: boolean;
  // tool_call
  arguments?: Record<string, unknown> | null;
  // thought / final / plan / error
  output?: Record<string, unknown> | null;
}

/** A step from GET /runs/{id} — the complete record. */
export interface StoredStep {
  idx: number;
  kind: StepKind;
  tool_name: string | null;
  output: Record<string, unknown> | null;
  error: string | null;
}

export interface RunDetail {
  run_id: number;
  status: RunStatus;
  goal: string;
  mode: string | null;
  provider: string | null;
  model: string | null;
  answer: string | null;
  error: string | null;
  step_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  elapsed_seconds: number | null;
  created_at: string | null;
  steps: StoredStep[];
}

export interface RunSummary {
  run_id: number;
  status: RunStatus;
  goal: string;
  mode: string | null;
  provider: string | null;
  model: string | null;
  step_count: number;
  total_tokens: number;
  elapsed_seconds: number | null;
  created_at: string | null;
}

export interface CreateRunResponse {
  run_id: number;
  status: RunStatus;
  mode: string;
  provider: string;
  model: string;
}

export interface ModelInfo {
  id: string;
  label: string;
  supports_tools: boolean;
  notes: string | null;
}

export interface ProviderInfo {
  id: string;
  label: string;
  blurb: string;
  requires_key: boolean;
  allows_custom_base_url: boolean;
  keys_url: string | null;
  default_model: string | null;
  models: ModelInfo[];
}

export interface TestResult {
  ok: boolean;
  provider: string;
  model: string | null;
  latency_ms: number | null;
  /** The check that matters. Some models accept a `tools` parameter and
   *  silently ignore it — the agent then appears to refuse every task, with no
   *  error anywhere. `ok: true` with `tool_calling: false` is a real failure
   *  and must be shown as one. */
  tool_calling: boolean | null;
  error: string | null;
  reason: string | null;
}

/** BYOK configuration. Held in sessionStorage, sent with each run.
 *  Never persisted server-side. */
export interface ProviderConfig {
  provider: string;
  api_key: string;
  model?: string | null;
  base_url?: string | null;
}

/** The backend's typed refusal reasons. They deserve different screens:
 *  "out of trial runs" is a conversion moment, "invalid key" is a fix-it
 *  moment, and a generic error message conflates them. */
export type RefusalReason =
  | "trial_exhausted"
  | "hosted_unconfigured"
  | "missing_key"
  | "unknown_provider"
  | "unsafe_base_url"
  | "no_model"
  | "no_adapter"
  | "no_provider";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public reason?: RefusalReason,
  ) {
    super(message);
  }
}
