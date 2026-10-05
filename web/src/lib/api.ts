import { getIdentity } from "./storage";

const ORIGIN = import.meta.env.VITE_API_URL || "http://localhost:8000";

// A comma here is the single most likely misconfiguration — VITE_API_URL is
// one URL, unlike CORS_ORIGINS (a genuinely comma-separated setting one
// container over), so it's an easy pattern to assume applies here too. Left
// unchecked, this produces a URL like "https://a.example,http://b.example"
// that the browser can't resolve, surfacing as an opaque DNS error
// (NS_ERROR_UNKNOWN_HOST in Firefox) with no indication of the real cause.
if (ORIGIN.includes(",")) {
  console.error(
    `VITE_API_URL is malformed — it should be exactly one URL, not a comma-separated list: "${ORIGIN}". ` +
      "Fix it in .env and rebuild (make build) — this is baked in at build time, not read at container startup.",
  );
}

class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

// DRF returns two different shapes depending on the error: framework-level
// exceptions (permission, auth, throttling) return {"detail": "..."}, but
// serializer validation errors — by far the most common case, hit on every
// bad expires_hours, invalid report reason, missing field — return
// {"field_name": ["message", ...]} with no "detail" key at all. Reading only
// .detail means every validation error silently falls back to a generic
// "Bad Request", which defeats the point of surfacing real server errors.
function extractErrorMessage(body: unknown): string | null {
  if (!body) return null;
  // Checked before the generic object-keyed logic below, not folded into
  // it — a plain-string DRF ValidationError (raised directly in a view,
  // not tied to a specific field) serializes to a bare array of strings,
  // not {detail: "..."}. typeof [] === "object" in JS, so without this
  // check first, the loop below ran anyway, treating the array's own
  // numeric indices as if they were field names — producing "0: Incorrect
  // access code." instead of just "Incorrect access code.". Confirmed
  // empirically, not just traced: this is what StatusView's POST
  // (deaddrop/views.py) actually returns for a wrong access code, and the
  // same pattern is used extensively elsewhere in this backend
  // (raise ValidationError("some message") with no field name).
  if (Array.isArray(body)) {
    return body.length ? body.map(String).join(" ") : null;
  }
  if (typeof body !== "object") return null;
  const obj = body as Record<string, unknown>;
  if (typeof obj.detail === "string") return obj.detail;

  const parts: string[] = [];
  for (const [key, value] of Object.entries(obj)) {
    const messages = Array.isArray(value) ? value : [value];
    const prefix = key === "non_field_errors" ? "" : `${key}: `;
    for (const m of messages) parts.push(`${prefix}${m}`);
  }
  return parts.length ? parts.join(" ") : null;
}

interface RequestOpts {
  query?: string;
  body?: unknown;
  formData?: FormData;
  ownerSecret?: string;
  /** A drop's password, sent as a header rather than a query parameter — a
   * query string ends up in access logs, browser history, and any proxy or
   * CDN that logs request URLs; a header doesn't get captured by any of
   * those by default. See drops/views.py: DropViewSet.retrieve(). */
  dropPassword?: string;
  /** Only attach identity signature headers when a request genuinely needs
   * one (creating/managing a drop as your identity, "my drops"). Plain
   * browsing never signs — having an identity shouldn't deanonymize every
   * page you view. */
  sign?: boolean;
}

async function request(method: string, path: string, opts: RequestOpts = {}) {
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;
  // Sentinel for GET/DELETE (no body) and for multipart uploads, which can't
  // practically be hashed client-side — the browser builds the exact
  // multipart byte stream internally and there's no API to intercept it
  // before send. Must match the server's convention exactly (see auth.py).
  let bodyForHash = "";

  if (opts.formData) {
    body = opts.formData;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    // Serialize exactly once. Hashing a separately-serialized copy for
    // signing while sending a different one would silently reopen the same
    // hole this hash exists to close.
    bodyForHash = JSON.stringify(opts.body);
    body = bodyForHash;
  }

  if (opts.ownerSecret) headers["X-Owner-Secret"] = opts.ownerSecret;
  if (opts.dropPassword) headers["X-Drop-Password"] = opts.dropPassword;

  if (opts.sign) {
    const identity = getIdentity();
    if (!identity) throw new Error("No identity on this device to sign with.");
    if (!crypto.subtle) throw new Error("Signed actions need a secure connection (HTTPS, or localhost while developing).");
    // Loaded on demand: keeps tweetnacl out of the initial bundle, since
    // most visits (browsing, viewing a drop) never sign anything.
    const { sha256Hex, signRequest } = await import("./crypto");
    const ts = Math.floor(Date.now() / 1000);
    const bodyHash = await sha256Hex(bodyForHash);
    headers["X-Public-Key"] = identity.publicKey;
    headers["X-Timestamp"] = String(ts);
    headers["X-Signature"] = signRequest(identity.secretKey, method, path, ts, bodyHash); // path only — no query string
  }

  const url = `${ORIGIN}${path}${opts.query ? `?${opts.query}` : ""}`;
  // credentials: "include" — without this, fetch() defaults to only
  // sending cookies same-origin, and the frontend/API run on different
  // origins in any real deployment. The private-access grant (see
  // deaddrop/private_access.py) is a cookie; without this, it would
  // never actually be sent back after being set, and every request past
  // the first would still be rejected — an infinite login loop. Every
  // other call in this file is already signature-based, not cookie-based,
  // so this has no effect on them beyond additionally sending whatever
  // cookie exists, which was previously nothing at all.
  const res = await fetch(url, { method, headers, body, credentials: "include" });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(extractErrorMessage(body) || res.statusText, res.status, body);
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  // Always a 200 — cooling state lives in the body, not the HTTP status,
  // specifically so this one call never throws through the normal
  // request() error path. App.tsx polls this directly and independently
  // of whatever else is happening on the current page, rather than
  // relying on incidentally hitting a 503 from some other action — that
  // would mean the cooling screen never shows up on a page that doesn't
  // happen to make any other API call right away.
  getStatus: () => request("GET", "/api/status/"),

  // POST to the same URL as getStatus — both are "what should the
  // frontend show instead of the normal app" concerns on the backend
  // too, not two unrelated things that happened to share a file.
  submitPrivateAccessCode: (code: string) => request("POST", "/api/status/", { body: { code } }),

  listDrops: (lat: number, lng: number, radius = 50000) =>
    request("GET", "/api/drops/", { query: `lat=${lat}&lng=${lng}&radius=${radius}` }),

  // Up to 50 drops by ID in one request — see DropViewSet.get_queryset.
  dropsByIds: (ids: string[]) => request("GET", "/api/drops/", { query: `ids=${ids.join(",")}` }),

  myDrops: () => request("GET", "/api/drops/", { query: "mine=1", sign: true }),

  getDrop: (id: string, password?: string, ownerSecret?: string, asIdentity = false) =>
    request("GET", `/api/drops/${id}/`, { dropPassword: password, ownerSecret, sign: asIdentity }),

  createDrop: (data: object, asIdentity = false) => request("POST", "/api/drops/", { body: data, sign: asIdentity }),

  updateDrop: (id: string, data: object, ownerSecret?: string, asIdentity = false) =>
    request("PATCH", `/api/drops/${id}/`, { body: data, ownerSecret, sign: asIdentity }),

  deleteDrop: (id: string, ownerSecret?: string, asIdentity = false) =>
    request("DELETE", `/api/drops/${id}/`, { ownerSecret, sign: asIdentity }),

  collectDrop: (id: string, password?: string) =>
    request("POST", `/api/drops/${id}/collect/`, { body: { password } }),

  reportDrop: (id: string, reason: string, details = "") =>
    request("POST", `/api/drops/${id}/report/`, { body: { reason, details } }),

  listMessages: (dropId: string, password?: string) =>
    request("GET", `/api/drops/${dropId}/messages/`, { dropPassword: password }),

  postMessage: (dropId: string, ciphertext: string, password?: string) =>
    request("POST", `/api/drops/${dropId}/messages/`, { body: { ciphertext }, dropPassword: password }),

  // Server-side gated on DEPLOYMENT_MODE=private (see settings.py) — this
  // call will 403 on a public instance regardless of what's passed here.
  // No forced signing: identity-per-upload was tried as an accountability
  // measure and deliberately removed (a throwaway keypair defeats it for
  // free), so this follows the same ownership rules as everything else —
  // asIdentity when the drop itself is identity-managed, ownerSecret
  // otherwise.
  uploadPhoto: (dropId: string, file: File, ownerSecret?: string, asIdentity = false) => {
    const fd = new FormData();
    fd.append("image", file);
    return request("POST", `/api/drops/${dropId}/photos/`, { formData: fd, ownerSecret, sign: asIdentity });
  },

  uploadVideo: (dropId: string, file: File, ownerSecret?: string, asIdentity = false) => {
    const fd = new FormData();
    fd.append("video", file);
    return request("POST", `/api/drops/${dropId}/videos/`, { formData: fd, ownerSecret, sign: asIdentity });
  },

  getMe: () => request("GET", "/api/identities/me/", { sign: true }),
  updateMe: (displayName: string) => request("PATCH", "/api/identities/me/", { body: { display_name: displayName }, sign: true }),
};
