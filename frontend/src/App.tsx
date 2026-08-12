import { Route, Routes, useLocation } from "react-router-dom";
import { NavRail } from "./components/NavRail";
import { Home } from "./pages/Home";
import { RunView } from "./pages/RunView";
import { Settings } from "./pages/Settings";

export default function App() {
  const location = useLocation();

  return (
    <div className="flex h-full flex-col md:flex-row">
      <NavRail />
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
