import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";

import CoolingScreen from "./components/CoolingScreen";
import ErrorBoundary from "./components/ErrorBoundary";
import PrivateAccessScreen from "./components/PrivateAccessScreen";
import Spinner from "./components/Spinner";
import { api } from "./lib/api";
import { ConfirmProvider } from "./lib/confirm";
import { ToastProvider } from "./lib/toast";
import { useVisibleInterval } from "./lib/useVisibleInterval";

// Lazy, not static imports — Home pulls in Leaflet (a substantial mapping
// library) and DropDetail pulls in qrcode.react, neither of which the
// other pages need at all. Static imports meant every visitor downloaded,
// parsed, and executed the full weight of every page's dependencies
// upfront, in one bundle, regardless of which route they actually landed
// on — someone opening a shared drop link straight to /drop/:id paid for
// Leaflet they might never load that session. Vite/Rollup handles the
// separate-chunk-per-dynamic-import splitting automatically; no extra
// build config needed for this.
const Home = lazy(() => import("./pages/Home"));
const CreateDrop = lazy(() => import("./pages/CreateDrop"));
const DropDetail = lazy(() => import("./pages/DropDetail"));
const Identity = lazy(() => import("./pages/Identity"));

const navClass = ({ isActive }: { isActive: boolean }) => (isActive ? "active" : undefined);

export default function App() {
  const location = useLocation();
  const [cooling, setCooling] = useState<{ until: string | null; message: string } | null>(null);
  const [needsPrivateAccess, setNeedsPrivateAccess] = useState(false);

  // useCallback with empty deps — not just a plain function inside the
  // effect below — specifically so this has a stable reference across
  // every App re-render. Passed as CoolingScreen's onExpire prop: without
  // a stable reference, every unrelated App re-render would hand
  // CoolingScreen a *new* function, which — since it's in useCountdown's
  // own effect dependency array — would reset that countdown's timer and
  // recreate its interval far more often than the actual, occasional
  // "cooling started or ended" changes that should trigger it. Also
  // passed to PrivateAccessScreen as onGranted, for the same reason —
  // re-checking status right after a correct code submission is what
  // actually clears needsPrivateAccess, rather than waiting for the next
  // scheduled 30-second poll.
  const checkStatus = useCallback(async () => {
    try {
      const s = await api.getStatus();
      setCooling(s.cooling ? { until: s.cooling_until, message: s.message } : null);
      setNeedsPrivateAccess(s.private_access_required && !s.private_access_granted);
    } catch {
      // A failed status check itself isn't reason to show cooling — that
      // would mean a transient network blip locks everyone out of an
      // instance that's actually fine. Leave whatever the last known
      // state was; the real enforcement is CoolingMiddleware (and
      // PrivateAccessMiddleware) on every other request regardless of
      // whether this specific check succeeds.
    }
  }, []);

  useEffect(() => {
    checkStatus();
  }, [checkStatus]);
  // Enforcement is at the backend; this poll only shows the cooling screen
  // promptly to someone already using the app. CoolingScreen's onExpire
  // handles the other direction. Paused while the tab is hidden.
  useVisibleInterval(checkStatus, 30000);

  // Checked before cooling, not after — "you're not authorized to be
  // here" is the more fundamental rejection, matching the same
  // precedence PrivateAccessMiddleware has over CoolingMiddleware on the
  // backend (see deaddrop/settings.py's MIDDLEWARE list ordering).
  if (needsPrivateAccess) {
    return (
      <ToastProvider>
        <ConfirmProvider>
          <div className="app">
            <PrivateAccessScreen onGranted={checkStatus} />
          </div>
        </ConfirmProvider>
      </ToastProvider>
    );
  }

  if (cooling) {
    return (
      <ToastProvider>
        <ConfirmProvider>
          <div className="app">
            <CoolingScreen until={cooling.until} message={cooling.message} onExpire={checkStatus} />
          </div>
        </ConfirmProvider>
      </ToastProvider>
    );
  }

  return (
    <ToastProvider>
      <ConfirmProvider>
        <div className="app">
          <nav className="nav">
            {/* `end` on the root link only — without it, NavLink's default
             * matching treats "/" as a prefix of every route, so "Nearby"
             * would show active on every page, not just its own. */}
            <NavLink to="/" end className={navClass}>
              Nearby
            </NavLink>
            <NavLink to="/create" className={navClass}>
              Create Drop
            </NavLink>
            <NavLink to="/identity" className={navClass}>
              Identity
            </NavLink>
          </nav>
          <main>
            {/* Keyed on the path so navigating away from a crashed page gets
             * a fresh boundary instance — otherwise the same instance stays
             * mounted across route changes (only its children swap), and a
             * caught error would keep showing even on an unrelated, working
             * page until "Try again" was clicked. */}
            <ErrorBoundary key={location.pathname}>
              {/* A fallback per navigation, not one persistent shell spinner —
               * Suspense only actually shows this the first time a given
               * page's chunk is fetched; once cached by the browser,
               * subsequent visits mount instantly with nothing shown here. */}
              <Suspense
                fallback={
                  <p className="loading-line">
                    <Spinner /> Loading…
                  </p>
                }
              >
                <Routes>
                  <Route path="/" element={<Home />} />
                  <Route path="/create" element={<CreateDrop />} />
                  <Route path="/drop/:id" element={<DropDetail />} />
                  <Route path="/identity" element={<Identity />} />
                </Routes>
              </Suspense>
            </ErrorBoundary>
          </main>
        </div>
      </ConfirmProvider>
    </ToastProvider>
  );
}
