import { NavLink, Route, Routes } from "react-router-dom";
import BreakIt from "./pages/BreakIt";
import Evals from "./pages/Evals";
import MyRuns from "./pages/MyRuns";
import RunView from "./pages/RunView";
import SystemPage from "./pages/System";
import Trace from "./pages/Trace";
import TryIt from "./pages/TryIt";

const links = [
  ["/", "Try it"],
  ["/break", "Break it"],
  ["/evals", "Eval results"],
  ["/runs", "My runs"],
  ["/system", "System"],
] as const;

export default function App() {
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
      <main id="main" className="flex-1 mx-auto w-full max-w-6xl px-4 py-6">
        <Routes>
          <Route path="/" element={<TryIt />} />
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
