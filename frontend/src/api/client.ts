import { ensureIdentity, storedToken } from "./identity";
import {
  ApiError,
  type CreateRunResponse,
  type ProviderConfig,
  type ProviderInfo,
  type RefusalReason,
  type RunDetail,
  type RunSummary,
  type TestResult,
} from "./types";

// Baked in at BUILD time by Vite, not read at runtime — changing it requires a
// redeploy, not just an env edit on Vercel.
const BASE = import.meta.env.VITE_API_BASE_URL ?? "";

/**
 * credentials: "include" is NOT optional.
 *
 * Runs are owned by an httpOnly identity cookie. Without it every request
 * arrives as a brand-new visitor, so POST /runs succeeds and the follow-up
 * GET /runs/{id} returns 404 — and the symptom points nowhere near cookies.
 *
 * This also requires the backend to allow credentials, which it only does when
 * CORS_ALLOWED_ORIGINS names a real origin. A wildcard silently disables them.
 */
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");

  // The identity is sent as an explicit header, not left to a cookie the
  // browser may block. ensureIdentity() fetches one on first use.
  const token = await ensureIdentity();
  headers.set("X-Identity", token);

  let response: Response;
  try {
    response = await fetch(`${BASE}/api/v1${path}`, {
      ...init,
      headers,
      credentials: "include",
    });
  } catch {
    // A network failure here usually means the backend is cold-starting.
    // Render's free tier sleeps after ~15 minutes and takes ~50s to wake, so
    // saying that is more useful than "failed to fetch".
    throw new ApiError(
      "Could not reach the backend. It may be waking up — this can take up to a minute on the free tier.",
      0,
    );
  }

  if (response.status === 204) return undefined as T;

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    // The backend returns {detail: {message, reason}} for typed refusals and
    // {detail: "..."} for everything else.
    const detail = body?.detail;
    const message =
      typeof detail === "object" && detail?.message
        ? detail.message
        : typeof detail === "string"
          ? detail
          : `Request failed (${response.status})`;
    const reason: RefusalReason | undefined =
      typeof detail === "object" ? detail?.reason : undefined;
    throw new ApiError(message, response.status, reason);
  }

  return body as T;
}

export const api = {
  createRun: (goal: string, byok?: ProviderConfig | null) =>
    request<CreateRunResponse>("/runs", {
      method: "POST",
      body: JSON.stringify({ goal, byok: byok ?? null }),
    }),

  getRun: (runId: number) => request<RunDetail>(`/runs/${runId}`),

  listRuns: (limit = 30) =>
    request<{ runs: RunSummary[]; total: number }>(`/runs?limit=${limit}`),

  cancelRun: (runId: number) =>
    request<RunDetail>(`/runs/${runId}/cancel`, { method: "POST" }),

  providers: () => request<ProviderInfo[]>("/providers"),

  testProvider: (config: ProviderConfig) =>
    request<TestResult>("/providers/test", {
      method: "POST",
      body: JSON.stringify(config),
    }),
};

/** The SSE endpoint URL, with the identity token as a query parameter.
 *
 *  EventSource cannot send custom headers, so the token that other requests
 *  pass in X-Identity has to ride in the URL here. By the time a run is
 *  streamed it was already created (which called ensureIdentity), so the token
 *  is in storage. The cookie is still sent via withCredentials as a fallback
 *  where third-party cookies are allowed. */
export const streamUrl = (runId: number) => {
  const token = storedToken();
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${BASE}/api/v1/runs/${runId}/events${query}`;
};
