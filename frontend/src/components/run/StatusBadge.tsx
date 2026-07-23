import type { RunStatus } from "@/api/types";
import { cn } from "@/lib/utils";

const STYLES: Record<string, string> = {
  connecting: "text-[var(--color-muted)]",
  created: "text-[var(--color-muted)]",
  running: "text-[var(--color-accent)]",
  completed: "text-[var(--color-ok)]",
  failed: "text-[var(--color-bad)]",
  cancelled: "text-[var(--color-warn)]",
  timed_out: "text-[var(--color-warn)]",
};

export function StatusBadge({ status }: { status: RunStatus | "connecting" }) {
  const live = status === "running" || status === "connecting";
  return (
    <span className={cn("flex items-center gap-1.5 text-xs", STYLES[status] ?? "text-[var(--color-muted)]")}>
      <span className={cn("h-1.5 w-1.5 rounded-full bg-current", live && "animate-breathe")} />
      {status}
    </span>
  );
}
