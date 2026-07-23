import { useCallback, useState } from "react";
import type { ProviderConfig } from "@/api/types";

const KEY = "campusagent.provider";

/**
 * BYOK config, held in sessionStorage.
 *
 * Why sessionStorage and not localStorage: it dies with the tab, which matches
 * "saved for this session" honestly. localStorage would persist a credential
 * indefinitely on a shared machine with no expiry.
 *
 * Why not memory-only: re-entering a key on every refresh makes people paste
 * it into a text file instead, which is worse in practice than the thing it
 * was meant to avoid.
 *
 * The key is still sent to the backend with each run — it has to be, the
 * server makes the provider call. The UI must say that plainly rather than
 * implying the key never leaves the browser.
 */
export function useProvider() {
  const [config, setConfigState] = useState<ProviderConfig | null>(() => {
    try {
      const raw = sessionStorage.getItem(KEY);
      return raw ? (JSON.parse(raw) as ProviderConfig) : null;
    } catch {
      return null;
    }
  });

  const setConfig = useCallback((next: ProviderConfig | null) => {
    setConfigState(next);
    try {
      if (next) sessionStorage.setItem(KEY, JSON.stringify(next));
      else sessionStorage.removeItem(KEY);
    } catch {
      // Private browsing can refuse storage. The config still works for this
      // page load; it just will not survive a refresh.
    }
  }, []);

  return { config, setConfig, hasProvider: config != null };
}
