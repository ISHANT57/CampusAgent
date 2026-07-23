import { useEffect, useRef, useState } from "react";
import { api, streamUrl } from "@/api/client";
import { TERMINAL, type RunStatus, type StreamStep, type StoredStep } from "@/api/types";

/** Consecutive transport failures before abandoning SSE for polling.
 *  Low, because a stream that fails three times in a row is not coming back —
 *  and every retry is another second of a screen saying "resuming…". */
const MAX_STREAM_FAILURES = 3;
const POLL_MS = 1500;

export interface RunStreamState {
  steps: StreamStep[];
  status: RunStatus | "connecting";
  goal: string | null;
  provider: string | null;
  model: string | null;
  answer: string | null;
  error: string | null;
  /** True while the connection has dropped and the browser is retrying.
   *  Shown as "reconnecting", not as a failure — the run is still going. */
  reconnecting: boolean;
  /** Seconds since the last step arrived. Drives the live counter on the
   *  in-flight step, which is the whole answer to "is it stuck?". */
  idleSeconds: number;
}

/**
 * The one hook that matters: SSE -> a step list and a status.
 *
 * RECONNECTION NEEDS NO CODE HERE.
 * EventSource resends Last-Event-ID automatically, and the backend resumes
 * with `WHERE idx > n` because the trace is durable. Most SSE clients maintain
 * a replay buffer; this one does not need one, and that is a property of the
 * backend rather than a cleverness of the frontend.
 *
 * The idle timer exists because a real knowledge_search step took 6,888ms.
 * Six seconds of silence is exactly when someone decides it has hung, and a
 * spinner says "wait" without saying what for or how long.
 */
export function useRunStream(runId: number | null): RunStreamState {
  const [steps, setSteps] = useState<StreamStep[]>([]);
  const [status, setStatus] = useState<RunStatus | "connecting">("connecting");
  const [goal, setGoal] = useState<string | null>(null);
  const [provider, setProvider] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [answer, setAnswer] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [idleSeconds, setIdleSeconds] = useState(0);

  const lastActivity = useRef<number>(Date.now());
  const failures = useRef(0);
  const pollTimer = useRef<number | null>(null);

  useEffect(() => {
    if (runId == null) return;
    failures.current = 0;

    /** Polling fallback. Rebuilds the same step shape from the stored trace,
     *  so the timeline renders identically whether it arrived live or not. */
    const startPolling = () => {
      if (pollTimer.current != null) return;
      const tick = async () => {
        try {
          const detail = await api.getRun(runId);
          setGoal(detail.goal);
          setProvider(detail.provider);
          setModel(detail.model);
          setSteps(detail.steps.map(toStreamStep));
          setStatus(detail.status);
          setReconnecting(false);
          lastActivity.current = Date.now();
          if (TERMINAL.includes(detail.status)) {
            setAnswer(detail.answer);
            setError(detail.error);
            if (pollTimer.current != null) window.clearInterval(pollTimer.current);
            pollTimer.current = null;
          }
        } catch (e) {
          // The run genuinely is not ours or does not exist. Say so, rather
          // than showing "resuming…" indefinitely.
          setError((e as Error).message);
          setStatus("failed");
          if (pollTimer.current != null) window.clearInterval(pollTimer.current);
          pollTimer.current = null;
        }
      };
      void tick();
      pollTimer.current = window.setInterval(tick, POLL_MS);
    };

    // Reset: navigating between runs must not show the previous one's trace.
    setSteps([]);
    setStatus("connecting");
    setAnswer(null);
    setError(null);
    setReconnecting(false);
    lastActivity.current = Date.now();

    // withCredentials carries the identity cookie. Without it the stream is
    // someone else's request and the backend returns 404.
    const source = new EventSource(streamUrl(runId), { withCredentials: true });

    const touch = () => {
      lastActivity.current = Date.now();
      setIdleSeconds(0);
      setReconnecting(false);
    };

    source.addEventListener("run", (event) => {
      touch();
      const data = JSON.parse((event as MessageEvent).data);
      setGoal(data.goal);
      setProvider(data.provider);
      setModel(data.model);
      setStatus(data.status);
    });

    source.addEventListener("step", (event) => {
      touch();
      const step = JSON.parse((event as MessageEvent).data) as StreamStep;
      setStatus("running");
      setSteps((current) =>
        // Guard against a duplicate after a reconnect. The backend resumes
        // from Last-Event-ID so this should not happen, but a duplicated step
        // in the timeline is a confusing bug to chase later.
        current.some((s) => s.idx === step.idx) ? current : [...current, step],
      );
    });

    source.addEventListener("done", (event) => {
      touch();
      const data = JSON.parse((event as MessageEvent).data);
      setStatus(data.status);
      setAnswer(data.answer ?? null);
      setError(data.error ?? null);
      source.close();
    });

    source.addEventListener("error", (event) => {
      const raw = (event as MessageEvent).data;
      if (raw) {
        // An in-band error event from the server (run not found).
        setError(JSON.parse(raw).message ?? "Stream error");
        source.close();
        return;
      }

      // No data means a transport-level failure, and EventSource deliberately
      // hides the status code — a 404 and a dropped connection look identical
      // here. It just keeps retrying, which is why a broken stream showed
      // "resuming…" forever instead of failing.
      //
      // So: retry a few times (a real drop recovers), then give up on SSE and
      // poll the run endpoint instead. That covers a genuinely failing stream
      // AND the proxies that break event streams outright.
      failures.current += 1;
      setReconnecting(true);
      if (failures.current >= MAX_STREAM_FAILURES) {
        source.close();
        startPolling();
      }
    });

    const ticker = window.setInterval(() => {
      setIdleSeconds((Date.now() - lastActivity.current) / 1000);
    }, 250);

    return () => {
      window.clearInterval(ticker);
      if (pollTimer.current != null) window.clearInterval(pollTimer.current);
      pollTimer.current = null;
      source.close();
    };
  }, [runId]);

  return { steps, status, goal, provider, model, answer, error, reconnecting, idleSeconds };
}

export const isTerminal = (status: RunStatus | "connecting") =>
  status !== "connecting" && TERMINAL.includes(status);

/** Stored step -> the stream's summary shape.
 *
 *  The polling fallback must produce the SAME structure the SSE path does, or
 *  the timeline would need two rendering paths and they would drift. */
function toStreamStep(step: StoredStep): StreamStep {
  const output = step.output ?? {};
  const meta = (output.meta ?? {}) as Record<string, unknown>;
  const base = { idx: step.idx, kind: step.kind, tool: step.tool_name, error: step.error };

  if (step.kind === "observation") {
    return {
      ...base,
      ok: output.ok as boolean | undefined,
      unavailable: output.unavailable as boolean | undefined,
      count: (meta.count as number) ?? null,
      latency_ms: (meta.latency_ms as number) ?? null,
      preview: String(meta.rendered ?? output.data ?? ""),
      truncated: false, // the stored record is complete by definition
    };
  }
  if (step.kind === "tool_call") {
    return { ...base, arguments: (output as Record<string, unknown>) ?? {} };
  }
  return { ...base, output };
}
