import { useEffect, useRef, useState } from "react";
import { streamUrl } from "@/api/client";
import { TERMINAL, type RunStatus, type StreamStep } from "@/api/types";

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

  useEffect(() => {
    if (runId == null) return;

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
      // No data means a transport drop. EventSource is already retrying and
      // will resend Last-Event-ID, so this is "reconnecting", not "failed".
      setReconnecting(true);
    });

    const ticker = window.setInterval(() => {
      setIdleSeconds((Date.now() - lastActivity.current) / 1000);
    }, 250);

    return () => {
      window.clearInterval(ticker);
      source.close();
    };
  }, [runId]);

  return { steps, status, goal, provider, model, answer, error, reconnecting, idleSeconds };
}

export const isTerminal = (status: RunStatus | "connecting") =>
  status !== "connecting" && TERMINAL.includes(status);
