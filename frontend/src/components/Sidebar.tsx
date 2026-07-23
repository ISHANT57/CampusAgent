import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { PanelLeftClose, PanelLeftOpen, Plus, Settings as SettingsIcon } from "lucide-react";
import { api } from "@/api/client";
import type { RunSummary } from "@/api/types";
import { cn, formatWhen } from "@/lib/utils";

/** Run history, ChatGPT-style. Collapsible because the trace is the wide part
 *  of this app and on a laptop the sidebar competes with it. */
export function Sidebar({ refreshKey }: { refreshKey: number }) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [open, setOpen] = useState(() => window.innerWidth >= 1024);
  const location = useLocation();

  useEffect(() => {
    api.listRuns(40).then((r) => setRuns(r.runs)).catch(() => {});
  }, [refreshKey, location.pathname]);

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed left-3 top-3 z-20 rounded-lg p-2 text-[var(--color-muted)] hover:bg-[var(--color-surface)] hover:text-[var(--color-text)]"
        aria-label="Open sidebar"
      >
        <PanelLeftOpen size={18} />
      </button>
    );
  }

  return (
    <aside className="flex w-[260px] shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)]">
      <div className="flex items-center justify-between px-3 py-3">
        <Link to="/" className="text-sm font-semibold tracking-tight">
          CampusBrain <span className="text-[var(--color-accent)]">Agent</span>
        </Link>
        <button
          onClick={() => setOpen(false)}
          className="rounded p-1 text-[var(--color-faint)] hover:text-[var(--color-text)]"
          aria-label="Collapse sidebar"
        >
          <PanelLeftClose size={16} />
        </button>
      </div>

      <div className="px-3 pb-3">
        <Link
          to="/"
          className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] px-3 py-2 text-sm transition-colors hover:bg-[var(--color-surface-2)]"
        >
          <Plus size={15} /> New run
        </Link>
      </div>

      <nav className="flex-1 overflow-y-auto px-2">
        {runs.length > 0 && (
          <div className="px-2 pb-1 pt-2 text-[10px] uppercase tracking-wider text-[var(--color-faint)]">
            History
          </div>
        )}
        {runs.map((run) => {
          const active = location.pathname === `/runs/${run.run_id}`;
          return (
            <Link
              key={run.run_id}
              to={`/runs/${run.run_id}`}
              className={cn(
                "group block truncate rounded-lg px-2 py-2 text-sm transition-colors",
                active
                  ? "bg-[var(--color-surface-2)] text-[var(--color-text)]"
                  : "text-[var(--color-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]",
              )}
            >
              <div className="flex items-center gap-2">
                <StatusDot status={run.status} />
                <span className="truncate">{run.goal}</span>
              </div>
              <div className="pl-4 pt-0.5 text-[10px] text-[var(--color-faint)]">
                {formatWhen(run.created_at)}
              </div>
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-[var(--color-border)] p-3">
        <Link
          to="/settings"
          className="flex items-center gap-2 rounded-lg px-2 py-2 text-sm text-[var(--color-muted)] transition-colors hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
        >
          <SettingsIcon size={15} /> Provider
        </Link>
        {/* Runs are keyed to a browser cookie. Saying so beats letting someone
            discover it after clearing site data — there is no account to
            recover from. */}
        <p className="px-2 pt-2 text-[10px] leading-relaxed text-[var(--color-faint)]">
          History lives in this browser only.
        </p>
      </div>
    </aside>
  );
}

function StatusDot({ status }: { status: string }) {
  const colour =
    status === "completed"
      ? "bg-[var(--color-ok)]"
      : status === "running" || status === "created"
        ? "bg-[var(--color-accent)] animate-breathe"
        : status === "cancelled"
          ? "bg-[var(--color-warn)]"
          : "bg-[var(--color-bad)]";
  return <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", colour)} />;
}
