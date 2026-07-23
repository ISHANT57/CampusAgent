import { Link, Route, Routes } from "react-router-dom";
import { Settings as SettingsIcon } from "lucide-react";
import { Home } from "./pages/Home";
import { RunView } from "./pages/RunView";
import { Settings } from "./pages/Settings";

export default function App() {
  return (
    <div className="min-h-full">
      <nav className="border-b border-[var(--color-border)]">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
          <Link to="/" className="text-sm font-medium">
            CampusBrain <span className="text-[var(--color-accent)]">Agent</span>
          </Link>
          <Link
            to="/settings"
            className="text-[var(--color-muted)] hover:text-[var(--color-text)]"
            aria-label="Settings"
          >
            <SettingsIcon size={16} />
          </Link>
        </div>
      </nav>

      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/runs/:id" element={<RunView />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </div>
  );
}
