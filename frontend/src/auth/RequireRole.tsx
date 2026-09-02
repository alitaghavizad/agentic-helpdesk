import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { landingFor, useAuth } from "./AuthContext";

export function RequireRole({ role, children }: { role?: string; children: ReactNode }) {
  const { status, principal } = useAuth();
  if (status === "loading") return null;
  if (status === "signed-out" || !principal) return <Navigate to="/login" replace />;
  if (role && principal.role !== role) return <Navigate to={landingFor(principal)} replace />;
  return <>{children}</>;
}
