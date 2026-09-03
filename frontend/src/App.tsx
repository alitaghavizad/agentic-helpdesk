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
import { Tickets as AdminTickets } from "./pages/Admin/Tickets";
import { Users } from "./pages/Admin/Users";
import { Lessons } from "./pages/Admin/Lessons";
import { Audit } from "./pages/Admin/Audit";

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
        <Route path="tickets" element={<AdminTickets />} />
        <Route path="users" element={<Users />} />
        <Route path="lessons" element={<Lessons />} />
        <Route path="audit" element={<Audit />} />
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
