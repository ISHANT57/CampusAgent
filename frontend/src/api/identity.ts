// Anonymous identity, stored in localStorage and sent explicitly.
//
// Not a cookie, deliberately. The frontend (vercel.app) and API (onrender.com)
// are different sites, so a cookie has to be SameSite=None — a third-party
// cookie, which Incognito, Brave, Firefox strict, and "block third-party
// cookies" all refuse. That is why the app worked in one browser profile and
// not another. localStorage is always first-party and always permitted, so a
// token kept there and sent by hand works everywhere.

const BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const KEY = "campusagent.identity";

let inflight: Promise<string> | null = null;

export function storedToken(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

/**
 * Ensure a token exists, returning it. Fetches one from the backend on first
 * use and caches it. The in-flight promise is memoised so two early requests
 * racing on load do not mint two identities.
 *
 * Raw fetch, NOT the api client — the client calls this, so routing it back
 * through the client would recurse.
 */
export async function ensureIdentity(): Promise<string> {
  const existing = storedToken();
  if (existing) return existing;

  if (!inflight) {
    inflight = fetch(`${BASE}/api/v1/identity`, {
      // credentials so the backend can also honour an existing cookie where
      // one is allowed, returning the SAME identity rather than a new one.
      credentials: "include",
    })
      .then((r) => r.json())
      .then((d: { token: string }) => {
        try {
          localStorage.setItem(KEY, d.token);
        } catch {
          // Storage blocked (rare). The token still works for this page load;
          // it just will not persist across a reload.
        }
        inflight = null;
        return d.token;
      })
      .catch((e) => {
        inflight = null;
        throw e;
      });
  }
  return inflight;
}
