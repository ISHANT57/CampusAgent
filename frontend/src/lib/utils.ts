import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...inputs: ClassValue[]) => twMerge(clsx(inputs));

/** Latency, at a readable precision. Sub-second work is where the calculator
 *  lives; multi-second work is where the network lives. */
export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function formatSeconds(s: number | null | undefined): string {
  if (s == null) return "";
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export function formatWhen(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 1440) return `${Math.floor(mins / 60)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export interface DateGroup<T> {
  label: string;
  items: T[];
}

/** Buckets a list into Today / Yesterday / Earlier by calendar day, not a
 *  rolling 24h window — an item from 11pm yesterday and one from 1am today
 *  are 2 hours apart but belong in different buckets, which `formatWhen`'s
 *  minutes-based math doesn't distinguish. Order within each bucket is
 *  whatever order `items` was already in — the caller's job, not this one's. */
export function groupByDay<T extends { created_at: string | null }>(items: T[]): DateGroup<T>[] {
  const groups = new Map<string, T[]>();
  const todayKey = new Date().toDateString();
  const yesterdayKey = new Date(Date.now() - 86_400_000).toDateString();

  for (const item of items) {
    const key = item.created_at ? new Date(item.created_at).toDateString() : null;
    const label = key === todayKey ? "Today" : key === yesterdayKey ? "Yesterday" : "Earlier";
    const list = groups.get(label);
    if (list) list.push(item);
    else groups.set(label, [item]);
  }

  return ["Today", "Yesterday", "Earlier"]
    .filter((label) => groups.has(label))
    .map((label) => ({ label, items: groups.get(label) as T[] }));
}
