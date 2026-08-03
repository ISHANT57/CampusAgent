import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRunStream } from "./useRunStream";

vi.mock("@/api/client", () => ({
  api: { getRun: vi.fn() },
  streamUrl: (id: number) => `http://test/stream/${id}`,
}));
vi.mock("@/api/identity", () => ({
  ensureIdentity: vi.fn().mockResolvedValue("test-token"),
}));

import { api } from "@/api/client";

/** A minimal fake of the browser's EventSource — jsdom does not implement it.
 *  Supports exactly what the hook uses: addEventListener, close(), and a test
 *  hook (`emit`) to dispatch a synthetic server event. */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  closed = false;
  private listeners: Record<string, ((e: MessageEvent) => void)[]> = {};

  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: (e: MessageEvent) => void) {
    (this.listeners[type] ??= []).push(cb);
  }

  close() {
    this.closed = true;
  }

  emit(type: string, data?: unknown) {
    const event = { data: data === undefined ? undefined : JSON.stringify(data) } as MessageEvent;
    this.listeners[type]?.forEach((cb) => cb(event));
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("useRunStream", () => {
  it("applies run/step/done events in order and dedups a repeated step index", async () => {
    const { result, unmount } = renderHook(() => useRunStream(42));

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe("http://test/stream/42");

    act(() => {
      source.emit("run", { goal: "test goal", provider: "gemini", model: "flash", status: "running" });
    });
    expect(result.current.goal).toBe("test goal");
    expect(result.current.status).toBe("running");

    act(() => {
      source.emit("step", { idx: 0, kind: "tool_call", tool: "calculator", error: null });
    });
    act(() => {
      // A duplicate of idx 0 — the backend should not send this, but a
      // reconnect resending the tail of the buffer is exactly the case the
      // hook guards against, and a silent duplicate in the timeline is a
      // confusing bug to chase later.
      source.emit("step", { idx: 0, kind: "tool_call", tool: "calculator", error: null });
    });
    act(() => {
      source.emit("step", { idx: 1, kind: "observation", tool: "calculator", error: null, ok: true });
    });
    expect(result.current.steps).toHaveLength(2);
    expect(result.current.steps.map((s) => s.idx)).toEqual([0, 1]);

    act(() => {
      source.emit("done", { status: "completed", answer: "the answer", error: null });
    });
    expect(result.current.status).toBe("completed");
    expect(result.current.answer).toBe("the answer");
    expect(source.closed).toBe(true);

    unmount();
  });

  it("falls back to polling after three consecutive transport failures", async () => {
    vi.mocked(api.getRun).mockResolvedValue({
      run_id: 42,
      status: "running",
      goal: "test goal",
      mode: "byok",
      provider: "gemini",
      model: "flash",
      answer: null,
      error: null,
      step_count: 0,
      prompt_tokens: 0,
      completion_tokens: 0,
      elapsed_seconds: 1,
      created_at: null,
      steps: [],
    });

    const { unmount } = renderHook(() => useRunStream(42));

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const source = FakeEventSource.instances[0];

    // A transport-level failure carries no event data, unlike an in-band
    // server error — that is what tells the hook to retry rather than fail.
    act(() => {
      source.emit("error");
      source.emit("error");
      source.emit("error");
    });

    expect(source.closed).toBe(true);
    await waitFor(() => expect(api.getRun).toHaveBeenCalledWith(42));

    unmount();
  });
});
