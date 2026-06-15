import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { AppProvider } from "./store/AppContext";
import { Sidebar } from "./components/Sidebar";
import { Topbar } from "./components/Topbar";
import { MeetingControl } from "./components/MeetingControl";
import { Workspace } from "./components/Workspace";
import { LandingPage } from "./components/LandingPage";
import { VoiceEnrollment } from "./components/VoiceEnrollment";
import { api, ApiError } from "./api/client";
import { RedmineStatusBanner, type RedmineStatus } from "./components/RedmineStatusBanner";

type Me = {
  id: string;
  email: string;
  display_name: string | null;
  voice_enrolled: boolean;
};

type AuthState =
  | { kind: "loading" }
  | { kind: "anonymous" }
  | { kind: "authed"; user: Me };

/**
 * Top-level routing.
 *
 * Routes:
 *   /              landing page (anonymous) — if already logged-in → /app
 *   /onboard/voice voice enrollment — requires session, blocks /app until done
 *   /app           main workspace — requires session + voice_enrolled
 *
 * Auth state is fetched once at App mount via /auth/me and shared with all
 * three routes. Children call refreshAuth() to re-poll after a state-changing
 * action (logout, voice enroll, etc.).
 */
export default function App() {
  const [auth, setAuth] = useState<AuthState>({ kind: "loading" });

  const refreshAuth = async () => {
    try {
      const me = await api.auth.me();
      setAuth({ kind: "authed", user: me });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setAuth({ kind: "anonymous" });
      } else {
        // Backend unreachable / network down → treat as anonymous so the
        // landing page renders instead of a stuck spinner.
        console.warn("[auth/me] failed, falling back to anonymous:", e);
        setAuth({ kind: "anonymous" });
      }
    }
  };

  useEffect(() => {
    refreshAuth();
  }, []);

  if (auth.kind === "loading") {
    return (
      <div className="auth-loading">
        <div className="auth-loading__spinner" />
      </div>
    );
  }

  const authed = auth.kind === "authed";
  const user = authed ? auth.user : null;

  return (
    <Routes>
      {/* Landing — only for anonymous users. Logged-in users get bounced
          to /app (or /onboard/voice if they haven't enrolled yet). */}
      <Route
        path="/"
        element={
          authed
            ? <Navigate to={user!.voice_enrolled ? "/app" : "/onboard/voice"} replace />
            : <LandingPage />
        }
      />

      {/* Voice enrollment — must be authed; if already enrolled, skip ahead. */}
      <Route
        path="/onboard/voice"
        element={
          !authed
            ? <Navigate to="/" replace />
            : user!.voice_enrolled
              ? <Navigate to="/app" replace />
              : <VoiceEnrollment user={user!} onEnrolled={refreshAuth} />
        }
      />

      {/* Main workspace — requires authed + voice enrolled. */}
      <Route
        path="/app"
        element={
          !authed
            ? <Navigate to="/" replace />
            : !user!.voice_enrolled
              ? <Navigate to="/onboard/voice" replace />
              : <MainApp user={user!} />
        }
      />

      {/* Unknown route → landing (or its redirect). Keeps deep-links sane. */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

/** Bundled because Vite + AppProvider should mount once per session, not every
 * time the user navigates between auth views. */
function MainApp({ user }: { user: Me }) {
  const [redmine, setRedmine] = useState<RedmineStatus | null>(null);

  const probeRedmine = async () => {
    try {
      setRedmine(await api.redmine.status());
    } catch (e) {
      console.warn("[redmine/status] probe failed:", e);
      setRedmine(null);
    }
  };

  // Restore body's grid layout that the legacy app expects. AppProvider on its
  // own doesn't set this — the body:has(.lp) override removes the grid for
  // landing, so /app needs to flip it back.
  useEffect(() => {
    document.body.style.display = "";
    document.body.style.height = "";
    document.body.style.overflow = "";
    probeRedmine();
    // Re-probe when the tab regains focus (e.g. returning from the consent gate).
    const onFocus = () => probeRedmine();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  return (
    <AppProvider>
      <Sidebar />
      <div className="app">
        <Topbar user={user} />
        {redmine && <RedmineStatusBanner status={redmine} />}
        <MeetingControl />
        <Workspace />
      </div>
    </AppProvider>
  );
}

// Export the navigate helper for components that need it (e.g. Topbar logout
// no longer needs window.location since Router handles the URL change).
export function useAppNavigate() {
  return useNavigate();
}
