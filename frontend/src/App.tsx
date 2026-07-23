import { useState } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { Home } from "./pages/Home";
import { RunView } from "./pages/RunView";
import { Settings } from "./pages/Settings";

export default function App() {
  const location = useLocation();
  // Bumped on navigation so the sidebar refetches history after a new run,
  // without either component owning the other's state.
  const [refreshKey] = useState(0);

  return (
    <div className="flex h-full">
      <Sidebar refreshKey={refreshKey} />
      <main key={location.pathname} className="min-w-0 flex-1 overflow-y-auto">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/runs/:id" element={<RunView />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
