import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { landingFor, useAuth } from "./auth/AuthContext";
import { RequireRole } from "./auth/RequireRole";
import { NavBar } from "./components/NavBar";
import { Chat } from "./pages/Chat";
import { Login } from "./pages/Login";
import { Tickets } from "./pages/Tickets";
import { Overview } from "./pages/Admin/Overview";
import { Costs } from "./pages/Admin/Costs";
import { Traces } from "./pages/Admin/Traces";
import { Conversations } from "./pages/Admin/Conversations";
import { Approvals } from "./pages/Admin/Approvals";

/** NavBar plus the routed page. Only signed-in routes get a shell. */
function Shell() {
  return (
    <div className="min-h-screen bg-slate-50">
      <NavBar />
      <main className="mx-auto max-w-6xl p-6">
        <Outlet />
      </main>
    </div>
  );
}

/**
 * A screen this task does not build yet -- tasks 3-5 replace these one
 * route at a time. Keeping the route wired now (rather than leaving it
 * missing) is what lets RequireRole and the nav links be exercised end to
 * end before the data screens exist.
 */
function Placeholder({ title }: { title: string }) {
  return (
    <div className="rounded border border-dashed border-slate-300 p-8 text-center text-slate-500">
      <p className="text-sm font-medium text-slate-700">{title}</p>
      <p className="mt-1 text-sm">This screen is not built yet.</p>
    </div>
  );
}

function Home() {
  const { status, principal } = useAuth();
  if (status === "loading") return null;
  return <Navigate to={principal ? landingFor(principal) : "/login"} replace />;
}

function NotFound() {
  return (
    <div className="flex min-h-screen items-center justify-center text-slate-500">
      <p>Page not found.</p>
    </div>
  );
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Home />} />

      <Route element={<RequireRole><Shell /></RequireRole>}>
        <Route path="/chat" element={<Chat />} />
        <Route path="/tickets" element={<Tickets />} />
        <Route path="/tickets/:id" element={<Tickets />} />
      </Route>

      <Route path="/admin/*" element={<RequireRole role="admin"><Shell /></RequireRole>}>
        <Route index element={<Overview />} />
        <Route path="conversations" element={<Conversations />} />
        <Route path="traces" element={<Traces />} />
        <Route path="traces/:runId" element={<Traces />} />
        <Route path="approvals" element={<Approvals />} />
        <Route path="tickets" element={<Placeholder title="Admin tickets" />} />
        <Route path="users" element={<Placeholder title="Admin users" />} />
        <Route path="lessons" element={<Placeholder title="Admin lessons" />} />
        <Route path="audit" element={<Placeholder title="Admin audit" />} />
        <Route path="costs" element={<Costs />} />
        {/* path="/admin/*" matches and shadows the top-level "*" below for
            anything under /admin, so an unmatched child (e.g. /admin/typo)
            must get its own not-found here or <Outlet/> silently renders
            nothing. */}
        <Route path="*" element={<NotFound />} />
      </Route>

      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}

export default App;
