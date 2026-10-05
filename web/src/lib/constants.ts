// Read from build-time env vars where set (docker-compose.yml sources
// these from the SAME env var names the backend itself reads — one value
// an operator sets, both sides pick it up, instead of two names that have
// to be kept in sync by hand). Falls back to this project's own long-
// standing defaults if unset or unparseable. Previously these were plain
// hardcoded constants, independently redefined in three different files —
// exactly the shape of bug that's bitten this project before.
function intEnv(value: string | undefined, fallback: number): number {
  // >= 0, not > 0 — the backend applies no floor at all
  // (int(os.environ.get("MAX_PHOTOS_PER_DROP", 2)) accepts 0 directly),
  // and 0 is a real, valid operator choice: "video enabled, photos not"
  // on an otherwise-private deployment. Rejecting 0 here would silently
  // fall back to the non-zero default instead, showing "up to 2 photos"
  // and letting someone select them — only for the backend to correctly
  // reject every single one. Negative values still fall back, since
  // unlike 0 they don't express anything the backend treats as
  // meaningfully different and are more likely a typo than intent.
  const n = Number(value);
  return value && Number.isFinite(n) && n >= 0 ? n : fallback;
}

export const MAX_PHOTOS = intEnv(import.meta.env.VITE_MAX_PHOTOS, 2);
export const MAX_VIDEOS = intEnv(import.meta.env.VITE_MAX_VIDEOS, 1); // private-deployment-only — see DEPLOYMENT_MODE
export const MAX_DROP_LIFETIME_HOURS = intEnv(import.meta.env.VITE_MAX_DROP_LIFETIME_HOURS, 24 * 14);
