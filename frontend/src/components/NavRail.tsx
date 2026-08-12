import { useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Menu, Plus, Search, Settings as SettingsIcon, Sparkles } from "lucide-react";
import { api } from "@/api/client";
import type { RunSummary } from "@/api/types";
import { useProvider } from "@/hooks/useProvider";
import { cn, formatWhen, groupByDay } from "@/lib/utils";

/** History panel, always visible on desktop instead of an icon-triggered
 *  flyout: a persistent sidebar with search and date-grouped runs. Below md
 *  it becomes a top hamburger bar + drawer — a persistent panel that width
 *  cannot fit on a phone without squeezing the page, the exact bug an
 *  earlier pass here already fixed once for the old always-expanded
 *  sidebar. */
export function NavRail() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    api.listRuns(40).then((r) => setRuns(r.runs)).catch(() => {});
  }, [location.pathname]);

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  return (
    <>
      {/* --- mobile top bar, below md --------------------------------- */}
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface)] px-3 md:hidden">
        <button
          onClick={() => setMobileOpen(true)}
          aria-label="Open menu"
          className="flex h-11 w-11 items-center justify-center rounded-lg text-[var(--color-muted)] hover:bg-[var(--color-surface-2)]"
        >
          <Menu size={20} />
        </button>
        <Link to="/" className="flex items-center gap-1.5 text-sm font-semibold text-[var(--color-text)]">
          <Sparkles size={16} className="text-[var(--color-accent)]" />
          CampusBrain <span className="text-[var(--color-accent)]">Agent</span>
        </Link>
        <div className="w-11" aria-hidden="true" />
      </div>

      {mobileOpen && (
        <>
          <div
            onClick={() => setMobileOpen(false)}
            aria-hidden="true"
            className="fixed inset-0 z-40 bg-black/50 md:hidden"
          />
          <div className="fixed inset-y-0 left-0 z-50 flex w-80 flex-col bg-[var(--color-surface)] shadow-lg md:hidden">
            <SidebarContent runs={runs} onNavigate={() => setMobileOpen(false)} />
          </div>
        </>
      )}

      {/* --- desktop sidebar, md and up --------------------------------- */}
      <nav className="hidden w-80 shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] md:flex">
        <SidebarContent runs={runs} onNavigate={() => {}} />
      </nav>
    </>
  );
}

/** Shared between the desktop panel and the mobile drawer — identical
 *  content, `onNavigate` just closes the drawer on the mobile copy (a no-op
 *  on desktop, where there is nothing to close). */
function SidebarContent({ runs, onNavigate }: { runs: RunSummary[]; onNavigate: () => void }) {
  const [query, setQuery] = useState("");
  const { config, hasProvider } = useProvider();
  const location = useLocation();

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? runs.filter((r) => r.goal.toLowerCase().includes(q)) : runs;
  }, [runs, query]);

  const groups = useMemo(() => groupByDay(filtered), [filtered]);

  return (
    <div className="flex h-full flex-col">
      <Link
        to="/"
        onClick={onNavigate}
        className="flex items-center gap-2 px-4 py-4 text-sm font-semibold text-[var(--color-text)]"
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-[var(--color-accent-tint)] text-[var(--color-accent)]">
          <Sparkles size={15} />
        </span>
        <span className="font-heading">
          CampusBrain <span className="text-[var(--color-accent)]">Agent</span>
        </span>
      </Link>

      <div className="px-3">
        <Link
          to="/"
          onClick={onNavigate}
          className="flex items-center justify-center gap-2 rounded-xl bg-[var(--color-accent)] px-3 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[var(--color-accent-hover)]"
        >
          <Plus size={16} /> New query
        </Link>
      </div>

      <div className="relative mx-3 mt-3">
        <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-faint)]" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search runs"
          className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] py-2 pl-8 pr-3 text-sm outline-none placeholder:text-[var(--color-faint)] focus:border-[var(--color-border-strong)]"
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
        {runs.length === 0 ? (
          <p className="px-2 py-2 text-xs text-[var(--color-faint)]">No runs yet.</p>
        ) : groups.length === 0 ? (
          <p className="px-2 py-2 text-xs text-[var(--color-faint)]">No runs match "{query}".</p>
        ) : (
          groups.map((group) => (
            <div key={group.label} className="mb-3">
              <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-faint)]">
                {group.label}
              </div>
              {group.items.map((run) => {
                const active = location.pathname === `/runs/${run.run_id}`;
                return (
                  <Link
                    key={run.run_id}
                    to={`/runs/${run.run_id}`}
                    onClick={onNavigate}
                    className={cn(
                      "group flex items-center gap-2 rounded-lg px-2 py-2 text-sm transition-colors",
                      active
                        ? "bg-[var(--color-accent-tint)] text-[var(--color-text)]"
                        : "text-[var(--color-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]",
                    )}
                  >
                    <StatusDot status={run.status} />
                    <span className="min-w-0 flex-1 truncate">{run.goal}</span>
                    <span className="shrink-0 text-[10px] text-[var(--color-faint)]">
                      {formatWhen(run.created_at)}
                    </span>
                  </Link>
                );
              })}
            </div>
          ))
        )}
      </div>

      <Link
        to="/settings"
        onClick={onNavigate}
        className="flex items-center gap-2.5 border-t border-[var(--color-border)] px-4 py-3 hover:bg-[var(--color-surface-2)]"
      >
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--color-surface-2)] text-[var(--color-muted)]">
          <SettingsIcon size={15} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-[var(--color-text)]">
            {hasProvider ? config?.provider : "No provider connected"}
          </span>
          {/* BYOK, so there is no account/plan to show here — the honest
              equivalent is what actually determines what a run can do. */}
          <span className="block truncate text-xs text-[var(--color-faint)]">
            {hasProvider ? (config?.model ?? "default model") : "Connect a provider"}
          </span>
        </span>
      </Link>
    </div>
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
