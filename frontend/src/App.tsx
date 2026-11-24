import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
import BreakIt from "./pages/BreakIt";
import Evals from "./pages/Evals";
import MyRuns from "./pages/MyRuns";
import RunView from "./pages/RunView";
import HarnessPage from "./pages/Harness";
import StoryPage from "./pages/Story";
import SystemPage from "./pages/System";
import Trace from "./pages/Trace";
import TryIt from "./pages/TryIt";

const links = [
  ["/", "The harness"],
  ["/story", "Follow a request"],
  ["/try", "Try it"],
  ["/break", "Break it"],
  ["/evals", "Eval results"],
  ["/runs", "My runs"],
  ["/system", "System"],
] as const;

export default function App() {
  const [connected, setConnected] = useState<boolean | null>(null);
  useEffect(() => {
    api.connected().then(setConnected);
  }, []);
  return (
    <div className="min-h-screen flex flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:p-2 focus:bg-white">
        Skip to content
      </a>
      <header className="bg-indigo-900 text-white">
        <div className="mx-auto max-w-6xl px-4 py-3 flex flex-wrap items-center gap-6">
          <p className="font-semibold text-lg">
            Article 15 <span className="font-normal text-indigo-200">· a desk for GDPR access requests</span>
          </p>
          <nav aria-label="Main">
            <ul className="flex flex-wrap gap-1">
              {links.map(([to, label]) => (
                <li key={to}>
                  <NavLink
                    to={to}
                    end={to === "/"}
                    className={({ isActive }) =>
                      `rounded px-3 py-1.5 text-sm ${isActive ? "bg-white text-indigo-900" : "hover:bg-indigo-800"}`
                    }
                  >
                    {label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
        </div>
      </header>
      <div role="note" className="bg-amber-50 border-b border-amber-200 text-amber-900 text-sm">
        <p className="mx-auto max-w-6xl px-4 py-2">
          You are using an AI system. Ferlane and its customers are fictional: never type real personal data. Your
          session and its cases are deleted after 24 hours. No tracking cookies. This is a demonstration, not legal
          advice.
        </p>
      </div>
      {connected === false && (
        <div role="status" className="bg-slate-100 border-b border-slate-300 text-slate-800 text-sm">
          <p className="mx-auto max-w-6xl px-4 py-2">
            The live desk is not connected to this site yet. The first page shows the recorded runs; Try it, Break it
            and Follow a request need the desk (see docs/running.md).
          </p>
        </div>
      )}
      <main id="main" className="flex-1 mx-auto w-full max-w-6xl px-4 py-6">
        <Routes>
          <Route path="/" element={<HarnessPage />} />
          <Route path="/story" element={<StoryPage />} />
          <Route path="/try" element={<TryIt />} />
          <Route path="/break" element={<BreakIt />} />
          <Route path="/evals" element={<Evals />} />
          <Route path="/runs" element={<MyRuns />} />
          <Route path="/runs/:id" element={<RunView />} />
          <Route path="/runs/:id/trace" element={<Trace />} />
          <Route path="/system" element={<SystemPage />} />
        </Routes>
      </main>
      <footer className="border-t border-slate-200 text-sm text-slate-600">
        <p className="mx-auto max-w-6xl px-4 py-3">
          Privacy: the only cookie is an anonymous session for this browser. Requests you write are used only for that
          run and deleted with your session.
        </p>
      </footer>
    </div>
  );
}
