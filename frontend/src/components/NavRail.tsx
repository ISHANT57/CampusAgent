import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { History, Menu, Sparkles, User } from "lucide-react";
import { api } from "@/api/client";
import type { RunSummary } from "@/api/types";
import { cn, formatWhen } from "@/lib/utils";

/** Icon rail, Perplexity-style: Home/search (the single query flow this app
 *  has — there is no separate "search" destination to send it to), a History
 *  flyout over the run list Sidebar used to keep permanently expanded, and a
 *  Settings avatar. Below md it becomes a top bar with a hamburger drawer
 *  holding the same three destinations, never squeezing the page content —
 *  the mistake the old expandable sidebar made on narrow screens. */
export function NavRail() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    api.listRuns(40).then((r) => setRuns(r.runs)).catch(() => {});
  }, [location.pathname]);

  // Closing on navigation covers both the flyout and the drawer without
  // separate effects — whichever is open, moving to a new page means the
  // reason it was open no longer applies.
  useEffect(() => {
    setHistoryOpen(false);
    setMobileOpen(false);
  }, [location.pathname]);

  const inSearchFlow = location.pathname === "/" || location.pathname.startsWith("/runs/");

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
          <div className="fixed inset-y-0 left-0 z-50 flex w-72 flex-col bg-[var(--color-surface)] shadow-lg md:hidden">
            <RailNav runs={runs} inSearchFlow={inSearchFlow} onNavigate={() => setMobileOpen(false)} expanded />
          </div>
        </>
      )}

      {/* --- desktop icon rail, md and up ------------------------------ */}
      <nav className="relative hidden shrink-0 flex-col items-center border-r border-[var(--color-border)] bg-[var(--color-surface)] py-4 md:flex md:w-16">
        <Link
          to="/"
          aria-label="CampusBrain Agent — home"
          className="mb-6 flex h-9 w-9 items-center justify-center rounded-lg text-[var(--color-accent)] hover:bg-[var(--color-accent-tint)]"
        >
          <Sparkles size={20} />
        </Link>

        <RailIcon to="/" label="Home" active={inSearchFlow} icon={<Sparkles size={19} />} />
        <RailIcon
          label="History"
          icon={<History size={19} />}
          onClick={() => setHistoryOpen((v) => !v)}
          active={historyOpen}
        />

        <div className="flex-1" />

        <Link
          to="/settings"
          aria-label="Provider settings"
          title="Provider settings"
          className="flex h-9 w-9 items-center justify-center rounded-full bg-[var(--color-surface-2)] text-[var(--color-muted)] hover:text-[var(--color-text)]"
        >
          <User size={17} />
        </Link>

        {historyOpen && (
          <>
            <div
              onClick={() => setHistoryOpen(false)}
              aria-hidden="true"
              className="fixed inset-0 z-40"
            />
            <div className="absolute left-[calc(100%+8px)] top-16 z-50 max-h-[70vh] w-80 overflow-y-auto rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-2 shadow-lg">
              <HistoryList runs={runs} onNavigate={() => setHistoryOpen(false)} />
            </div>
          </>
        )}
      </nav>
    </>
  );
}

function RailIcon({
  to,
  label,
  icon,
  active,
  onClick,
}: {
  to?: string;
  label: string;
  icon: React.ReactNode;
  active?: boolean;
  onClick?: () => void;
}) {
  const content = (
    <span
      className={cn(
        "relative flex h-11 w-11 items-center justify-center rounded-lg transition-colors",
        active
          ? "text-[var(--color-accent)]"
          : "text-[var(--color-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]",
      )}
    >
      {/* The active indicator this whole rail borrows its language from:
          a rose bar on the left edge, not a filled background — it reads at
          a glance without competing with the icon itself. */}
      {active && (
        <span className="absolute -left-2 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full bg-[var(--color-accent)]" />
      )}
      {icon}
    </span>
  );

  return to ? (
    <Link to={to} aria-label={label} title={label} className="mb-1">
      {content}
    </Link>
  ) : (
    <button onClick={onClick} aria-label={label} title={label} className="mb-1">
      {content}
    </button>
  );
}

/** Shared between the desktop flyout and the mobile drawer — same three
 *  destinations, `expanded` just adds text labels for the wider drawer. */
function RailNav({
  runs,
  inSearchFlow,
  onNavigate,
  expanded,
}: {
  runs: RunSummary[];
  inSearchFlow: boolean;
  onNavigate: () => void;
  expanded?: boolean;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-4 py-3">
        <Link to="/" onClick={onNavigate} className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
          <Sparkles size={16} className="text-[var(--color-accent)]" />
          CampusBrain <span className="text-[var(--color-accent)]">Agent</span>
        </Link>
      </div>

      <Link
        to="/"
        onClick={onNavigate}
        className={cn(
          "mx-2 flex items-center gap-2 rounded-lg px-3 py-2 text-sm",
          inSearchFlow ? "bg-[var(--color-accent-tint)] text-[var(--color-accent)]" : "text-[var(--color-text)] hover:bg-[var(--color-surface-2)]",
        )}
      >
        <Sparkles size={16} /> New query
      </Link>

      <div className="mt-4 flex items-center gap-2 px-4 text-[10px] uppercase tracking-wider text-[var(--color-faint)]">
        <History size={12} /> History
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        <HistoryList runs={runs} onNavigate={onNavigate} />
      </div>

      <div className="border-t border-[var(--color-border)] p-3">
        <Link
          to="/settings"
          onClick={onNavigate}
          className="flex items-center gap-2 rounded-lg px-2 py-2 text-sm text-[var(--color-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
        >
          <User size={15} /> Provider settings
        </Link>
        {expanded && (
          <p className="px-2 pt-2 text-[10px] leading-relaxed text-[var(--color-faint)]">
            History lives in this browser only.
          </p>
        )}
      </div>
    </div>
  );
}

function HistoryList({ runs, onNavigate }: { runs: RunSummary[]; onNavigate: () => void }) {
  const location = useLocation();

  if (runs.length === 0) {
    return <p className="px-2 py-2 text-xs text-[var(--color-faint)]">No runs yet.</p>;
  }

  return (
    <>
      {runs.map((run) => {
        const active = location.pathname === `/runs/${run.run_id}`;
        return (
          <Link
            key={run.run_id}
            to={`/runs/${run.run_id}`}
            onClick={onNavigate}
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
    </>
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
