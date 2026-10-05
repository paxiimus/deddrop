import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import Modal from "../components/Modal";
import DropQRCode from "../components/DropQRCode";
import LoadingButton from "../components/LoadingButton";
import Spinner from "../components/Spinner";
import { api } from "../lib/api";
import { MAX_DROP_LIFETIME_HOURS, MAX_PHOTOS, MAX_VIDEOS } from "../lib/constants";
import { useConfirm } from "../lib/confirm";
import { decryptMessage, encryptMessage, generateMessageKey } from "../lib/crypto";
import { getIdentity, getMessageKey, getOwnerSecret, saveMessageKey } from "../lib/storage";
import { useToast } from "../lib/toast";

// Same flag, same reasoning, as CreateDrop.tsx — kept as an independent
// constant rather than a shared import since it's cheap to compute and a
// shared "current deployment mode" module would be overkill for one line.
const DEPLOYMENT_MODE = import.meta.env.VITE_DEPLOYMENT_MODE === "private" ? "private" : "public";

const REPORT_REASONS = [
  { value: "illegal", label: "Illegal content" },
  { value: "spam", label: "Spam / abuse" },
  { value: "unsafe", label: "Unsafe location" },
  { value: "other", label: "Other" },
] as const;

interface Drop {
  id: string;
  title: string;
  description: string;
  latitude: number | null;
  longitude: number | null;
  is_public: boolean;
  is_password_protected: boolean;
  status: "active" | "expired" | "collected";
  starts_at: string | null;
  expires_at: string | null;
  photos: { id: number; image: string }[];
  videos: { id: number; video: string }[];
}

interface Message {
  id: string;
  ciphertext: string;
  content: string;
  is_system: boolean;
}

function safeDecrypt(ciphertext: string, key: string) {
  try {
    return decryptMessage(ciphertext, key);
  } catch {
    return "🔒 could not decrypt";
  }
}

export default function DropDetail() {
  const { id } = useParams<{ id: string }>();
  const toast = useToast();
  const confirmDialog = useConfirm();
  const [drop, setDrop] = useState<Drop | null>(null);
  const [needsPassword, setNeedsPassword] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [messageKey, setMessageKey] = useState<string | null>(null);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportReason, setReportReason] = useState<(typeof REPORT_REASONS)[number]["value"]>("spam");
  const [reportDetails, setReportDetails] = useState("");
  // A drop can be managed via a one-time owner secret OR a signed identity —
  // there's no field on the drop itself that reveals "is this mine" for the
  // identity path, so we check membership in /drops/?mine=1 once we know the
  // viewer has an identity at all.
  const [isMineViaIdentity, setIsMineViaIdentity] = useState(false);

  // Edit mode. Password/expiry/schedule each have their own "change this?"
  // toggle — a plain text field left blank would be genuinely ambiguous
  // (does blank mean "leave it alone" or "clear it"?), so the toggle makes
  // the choice explicit instead of guessing.
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editIsPublic, setEditIsPublic] = useState(true);
  const [changePassword, setChangePassword] = useState(false);
  const [editPassword, setEditPassword] = useState("");
  const [changeExpiry, setChangeExpiry] = useState(false);
  const [editExpiresHours, setEditExpiresHours] = useState<number | "">("");
  const [changeStarts, setChangeStarts] = useState(false);
  const [editStartsHours, setEditStartsHours] = useState<number | "">("");
  const [savingEdit, setSavingEdit] = useState(false);

  // One state per distinct action a user can trigger, rather than a single
  // shared "busy" flag — several of these can plausibly be relevant at
  // once from the user's perspective (nothing stops someone editing while
  // an upload from a moment ago is still finishing), so collapsing them
  // into one flag would make unrelated buttons appear to freeze together.
  const [loadingDrop, setLoadingDrop] = useState(false); // Unlock / Retry only — see load()
  const [sending, setSending] = useState(false);
  const [submittingReport, setSubmittingReport] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [collecting, setCollecting] = useState(false);
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [uploadingVideo, setUploadingVideo] = useState(false);

  const ownerSecret = id ? getOwnerSecret(id) : undefined;
  const canManage = !!ownerSecret || isMineViaIdentity;
  const isPending = !!drop?.starts_at && new Date(drop.starts_at) > new Date();
  // Mirrors the backend's own Drop.is_expired property, computed fresh
  // from expires_at on every render rather than trusting the server's
  // stored status field, which can now genuinely lag actual expiry by up
  // to 5 minutes (expire_drops runs on that schedule; see the backend's
  // get_queryset comment). Without this, a drop that expired 2 minutes
  // ago but hasn't been caught by that task yet would still show its
  // "Mark as collected" button — clickable, and the backend would
  // correctly reject the attempt (it checks is_expired directly too),
  // but showing a button that's already known to fail if clicked is
  // worth correcting client-side when it's this cheap to do.
  const isActuallyExpired = !!drop?.expires_at && new Date(drop.expires_at) <= new Date();
  const effectiveStatus = drop?.status === "active" && isActuallyExpired ? "expired" : drop?.status;
  const shareUrl = messageKey ? `${location.origin}/drop/${id}#key=${messageKey}` : `${location.origin}/drop/${id}`;

  // draft lives in this same component's state, updated on every
  // keystroke while replying — without this memo, that re-render would
  // re-run messages.map() and re-decrypt every message on every single
  // keystroke, regardless of whether any of them actually changed. Real
  // crypto work (nacl.secretbox.open) repeated dozens of times a second
  // for an active thread, causing genuine, visible input lag — worse on
  // the mobile devices this PWA is actually meant to run on. Only
  // recomputes when messages or messageKey actually change.
  const decryptedMessages = useMemo(
    () =>
      messages.map((m) => ({
        id: m.id,
        isSystem: m.is_system,
        content: m.is_system ? m.content : messageKey ? safeDecrypt(m.ciphertext, messageKey) : "🔒 encrypted (no key)",
      })),
    [messages, messageKey],
  );

  useEffect(() => {
    if (!id) return;
    const fromHash = new URLSearchParams(location.hash.slice(1)).get("key");
    const key = fromHash || getMessageKey(id) || null;
    if (fromHash) saveMessageKey(id, fromHash);
    setMessageKey(key);
    load();

    if (getIdentity()) {
      api
        .myDrops()
        .then((mine: { id: string }[]) => setIsMineViaIdentity(mine.some((d) => d.id === id)))
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // After ownership is confirmed, refetch signed only if the server hid
  // this scheduled drop's location from the unsigned request.
  useEffect(() => {
    if (isMineViaIdentity && drop && isPending && drop.latitude == null) load(password || undefined, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMineViaIdentity, drop?.id]);

  async function load(pw?: string, asOwner = isMineViaIdentity) {
    if (!id) return;
    setLoadingDrop(true);
    try {
      // Unsigned unless this is known to be your own drop: signing every
      // view would link your identity to each drop you look at. The owner
      // secret (anonymous drops) is device-local and links nothing.
      const d = await api.getDrop(id, pw, ownerSecret, asOwner);
      setDrop(d);
      setNeedsPassword(false);
      setError("");
      setMessages(await api.listMessages(id, pw));
    } catch (e) {
      const status = (e as { status?: number }).status;
      const body = (e as { body?: { private_access_required?: boolean } }).body;
      // Checked before the drop-password branch below, not folded into
      // it — PrivateAccessMiddleware also rejects with 403, and without
      // this distinction, an unauthorized visitor hitting a direct drop
      // link (during the same brief window before App.tsx's own status
      // check resolves — see its handling of the analogous cooling-screen
      // race) would see this drop's *own* "password required" screen
      // instead of the actual problem, which has nothing to do with this
      // specific drop at all. App.tsx's poll corrects the whole screen
      // within that same window regardless; this just stops this page
      // from showing an actively misleading message in the meantime.
      if (body?.private_access_required) {
        setError((e as Error).message);
      } else if (status === 403) {
        setNeedsPassword(true);
        // Only a genuine attempt (pw was actually supplied) means the
        // password was wrong — the very first, argument-less call that
        // discovers a drop is locked at all also 403s, and that one isn't
        // an incorrect attempt, just a normal discovery step.
        if (pw) toast("Incorrect password.", "error");
      } else if (drop) {
        // load() also runs as a refresh after every mutating action (edit,
        // collect, message, photo upload). If just that refresh hits a
        // transient failure, the action itself already succeeded — blowing
        // away a working page over it and showing only a bare error would
        // discard perfectly good data still sitting in `drop`. Toast it and
        // keep showing what's already loaded instead.
        toast((e as Error).message || "Could not refresh.", "error");
      } else {
        setError((e as Error).message);
      }
    } finally {
      setLoadingDrop(false);
    }
  }

  async function sendMessage() {
    if (!id || !draft) return;
    let key = messageKey;
    if (!key) {
      key = generateMessageKey();
      saveMessageKey(id, key);
      setMessageKey(key);
    }
    setSending(true);
    try {
      await api.postMessage(id, encryptMessage(draft, key), password || undefined);
      setDraft("");
      // Only the thread changed — no need to refetch the drop as well.
      setMessages(await api.listMessages(id, password || undefined));
    } catch (e) {
      toast((e as Error).message || "Could not send that message.", "error");
    } finally {
      setSending(false);
    }
  }

  async function submitReport() {
    if (!id) return;
    setSubmittingReport(true);
    try {
      await api.reportDrop(id, reportReason, reportDetails);
      setReportOpen(false);
      setReportDetails("");
      toast("Reported. No identifying information was sent.", "success");
    } catch (e) {
      toast((e as Error).message || "Could not submit the report.", "error");
    } finally {
      setSubmittingReport(false);
    }
  }

  async function handleDelete() {
    if (!id) return;
    const ok = await confirmDialog("Delete this drop permanently? This cannot be undone.");
    if (!ok) return;
    setDeleting(true);
    try {
      await api.deleteDrop(id, ownerSecret, isMineViaIdentity);
      location.href = "/";
    } catch (e) {
      toast((e as Error).message || "Could not delete this drop.", "error");
      setDeleting(false);
    }
  }

  async function handlePhotoUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !id) return;
    setUploadingPhoto(true);
    try {
      await api.uploadPhoto(id, file, ownerSecret, isMineViaIdentity);
      await load(password || undefined);
    } catch (err) {
      toast((err as Error).message || "Could not upload that photo.", "error");
    } finally {
      e.target.value = "";
      setUploadingPhoto(false);
    }
  }

  async function handleVideoUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !id) return;
    setUploadingVideo(true);
    try {
      await api.uploadVideo(id, file, ownerSecret, isMineViaIdentity);
      await load(password || undefined);
    } catch (err) {
      toast((err as Error).message || "Could not upload that video.", "error");
    } finally {
      e.target.value = "";
      setUploadingVideo(false);
    }
  }

  function startEditing() {
    if (!drop) return;
    setEditTitle(drop.title);
    setEditDescription(drop.description);
    setEditIsPublic(drop.is_public);
    setChangePassword(false);
    setEditPassword("");
    setChangeExpiry(false);
    setEditExpiresHours("");
    setChangeStarts(false);
    setEditStartsHours("");
    setEditing(true);
  }

  async function submitEdit() {
    if (!id) return;
    setSavingEdit(true);
    try {
      const body: Record<string, unknown> = { title: editTitle, description: editDescription, is_public: editIsPublic };
      if (changePassword) body.password = editPassword;
      // Not `|| null` — that would treat a literally-typed 0 as blank,
      // silently sending "reset to the maximum" instead of the actual
      // value the user typed. CreateDrop.tsx already gets this right via
      // a typeof check; this didn't match it. The backend's own
      // min_value=1 validation will reject an actual 0 with a clear
      // error, which is the correct outcome — better than silently
      // substituting the opposite of what was typed.
      if (changeExpiry) body.expires_hours = editExpiresHours === "" ? null : editExpiresHours;
      if (changeStarts) body.starts_in_hours = editStartsHours || null;

      await api.updateDrop(id, body, ownerSecret, isMineViaIdentity);
      setEditing(false);
      await load(password || undefined);
      toast("Drop updated.", "success");
    } catch (e) {
      toast((e as Error).message || "Could not save those changes.", "error");
    } finally {
      setSavingEdit(false);
    }
  }

  async function handleCollect() {
    if (!id) return;
    setCollecting(true);
    try {
      await api.collectDrop(id, password || undefined);
      toast("Marked as collected.", "success");
      await load(password || undefined);
    } catch (e) {
      toast((e as Error).message || "Could not mark this as collected.", "error");
    } finally {
      setCollecting(false);
    }
  }

  if (needsPassword) {
    return (
      <div className="card">
        <h2>Password required</h2>
        <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" placeholder="Drop password" />
        <LoadingButton className="primary" loading={loadingDrop} loadingText="Checking…" onClick={() => load(password)}>
          Unlock
        </LoadingButton>
      </div>
    );
  }

  if (error) {
    return (
      <div className="card">
        <p className="error">{error}</p>
        <LoadingButton loading={loadingDrop} loadingText="Retrying…" onClick={() => load(password || undefined)}>
          Retry
        </LoadingButton>
      </div>
    );
  }
  if (!drop) {
    return (
      <p className="loading-line">
        <Spinner /> Loading…
      </p>
    );
  }

  if (editing) {
    return (
      <div className="card">
        <h2>Edit drop</h2>
        <input placeholder="Title" value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
        <textarea placeholder="Description" value={editDescription} onChange={(e) => setEditDescription(e.target.value)} />
        <label>
          <input type="checkbox" checked={editIsPublic} onChange={(e) => setEditIsPublic(e.target.checked)} /> Show in nearby
          list
        </label>

        <label>
          <input type="checkbox" checked={changePassword} onChange={(e) => setChangePassword(e.target.checked)} /> Change
          password
        </label>
        {changePassword && (
          <input
            type="password"
            placeholder="New password — leave blank to remove protection"
            value={editPassword}
            onChange={(e) => setEditPassword(e.target.value)}
          />
        )}

        <label>
          <input type="checkbox" checked={changeExpiry} onChange={(e) => setChangeExpiry(e.target.checked)} /> Change expiry
        </label>
        {changeExpiry && (
          <input
            type="number"
            placeholder={`Expires in hours from now — leave blank for the max, ${Math.round(MAX_DROP_LIFETIME_HOURS / 24)} days (no drop can outlive that)`}
            value={editExpiresHours}
            onChange={(e) => setEditExpiresHours(e.target.value === "" ? "" : Number(e.target.value))}
          />
        )}

        <label>
          <input type="checkbox" checked={changeStarts} onChange={(e) => setChangeStarts(e.target.checked)} /> Change
          schedule
        </label>
        {changeStarts && (
          <input
            type="number"
            placeholder="Starts in hours from now — leave blank to start immediately"
            value={editStartsHours}
            onChange={(e) => setEditStartsHours(e.target.value === "" ? "" : Number(e.target.value))}
          />
        )}

        <LoadingButton className="primary" loading={savingEdit} loadingText="Saving…" onClick={submitEdit}>
          Save changes
        </LoadingButton>
        <button onClick={() => setEditing(false)}>Cancel</button>
      </div>
    );
  }

  return (
    <div className="card">
      <h2>
        {drop.title || "Untitled drop"}
        {effectiveStatus !== "active" && <span className="status-badge"> · {effectiveStatus}</span>}
        {isPending && <span className="status-badge"> · starts {new Date(drop.starts_at as string).toLocaleString()}</span>}
      </h2>
      <p className="hint">
        {drop.is_public ? "Public — shows up in the nearby list." : "Private — only reachable by direct link, never listed."}
        {drop.is_password_protected && " Password-protected."}
        {drop.expires_at && ` Gone for good on ${new Date(drop.expires_at).toLocaleString()}.`}
      </p>
      <p>{drop.description}</p>

      {drop.latitude != null && drop.longitude != null && (
        <div className="form-section">
          <h3>Location</h3>
          <p className="mono">
            {drop.latitude}, {drop.longitude}
            <button
              type="button"
              className="copy-btn"
              onClick={() => navigator.clipboard.writeText(`${drop.latitude}, ${drop.longitude}`)}
            >
              Copy
            </button>
          </p>
          <a
            className="navigate-link"
            href={`https://www.openstreetmap.org/directions?to=${drop.latitude}%2C${drop.longitude}`}
            target="_blank"
            rel="noreferrer"
          >
            Navigate to this spot
          </a>
          <Link to={`/create?lat=${drop.latitude}&lng=${drop.longitude}`} className="hint">
            Reuse this location for a new drop
          </Link>
        </div>
      )}

      {drop.photos?.length > 0 && (
        <div className="form-section">
          <h3>Photos</h3>
          {drop.photos.map((p) => (
            <img key={p.id} src={p.image} className="photo" alt="" loading="lazy" decoding="async" />
          ))}
        </div>
      )}

      {drop.videos?.length > 0 && (
        <div className="form-section">
          <h3>Videos</h3>
          {drop.videos.map((v) => (
            // eslint-disable-next-line jsx-a11y/media-has-caption
            <video key={v.id} src={v.video} className="photo" controls preload="metadata" />
          ))}
        </div>
      )}

      <div className="form-section">
        <h3>Share</h3>
        <p className="mono">
          {shareUrl}
          <button type="button" className="copy-btn" onClick={() => navigator.clipboard.writeText(shareUrl)}>
            Copy
          </button>
        </p>
        <DropQRCode value={shareUrl} />
      </div>

      <div className="form-section">
        <h3>{drop.is_public ? "Messages" : "Exchange"}</h3>
        {!messageKey && (
          <p className="hint">
            No decryption key on this device — you can't read existing messages here. Sending one anyway starts a new,
            separate thread that only someone with the link below can read; it won't reach whoever has the original key.
          </p>
        )}
        {decryptedMessages.map((m) => (
          <div key={m.id} className={m.isSystem ? "msg system" : "msg"}>
            {m.content}
          </div>
        ))}
        <textarea value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Message…" />
        <LoadingButton className="primary" loading={sending} loadingText="Sending…" onClick={sendMessage}>
          Send (encrypted)
        </LoadingButton>
      </div>

      {isPending ? (
        <p className="hint">This drop hasn't started yet — it can't be marked collected until then.</p>
      ) : effectiveStatus !== "active" ? (
        <p className="hint">This drop is already {effectiveStatus}.</p>
      ) : (
        <>
          <p className="hint">
            Click this once you've actually found it. It doesn't delete anything — it just adds a note to the thread above
            and stops it from being collected again.
          </p>
          <LoadingButton loading={collecting} loadingText="Marking…" onClick={handleCollect}>
            Mark as collected
          </LoadingButton>
        </>
      )}
      <button onClick={() => setReportOpen(true)}>Report</button>

      {reportOpen && (
        <Modal onClose={() => setReportOpen(false)}>
          <h3>Report this drop</h3>
          {REPORT_REASONS.map((r) => (
            <label key={r.value} className="radio-row">
              <input
                type="radio"
                name="report-reason"
                value={r.value}
                checked={reportReason === r.value}
                onChange={() => setReportReason(r.value)}
              />
              {r.label}
            </label>
          ))}
          <textarea
            placeholder="Additional details (optional)"
            value={reportDetails}
            onChange={(e) => setReportDetails(e.target.value)}
          />
          <div className="row">
            <LoadingButton className="primary" loading={submittingReport} loadingText="Submitting…" onClick={submitReport}>
              Submit report
            </LoadingButton>
            <button onClick={() => setReportOpen(false)}>Cancel</button>
          </div>
        </Modal>
      )}

      {canManage && (
        <div className="owner-controls">
          <button onClick={startEditing}>Edit</button>
          {DEPLOYMENT_MODE === "private" && (
            <>
              {drop.photos.length < MAX_PHOTOS ? (
                <label className="file-label">
                  {uploadingPhoto ? (
                    <>
                      <Spinner size={14} /> Uploading…
                    </>
                  ) : (
                    `Add photo (${drop.photos.length}/${MAX_PHOTOS})`
                  )}
                  <input type="file" accept="image/*" onChange={handlePhotoUpload} disabled={uploadingPhoto} hidden />
                </label>
              ) : (
                <span className="hint">Photo limit reached ({MAX_PHOTOS}/{MAX_PHOTOS})</span>
              )}
              {drop.videos.length < MAX_VIDEOS ? (
                <label className="file-label">
                  {uploadingVideo ? (
                    <>
                      <Spinner size={14} /> Uploading…
                    </>
                  ) : (
                    `Add video (${drop.videos.length}/${MAX_VIDEOS})`
                  )}
                  <input type="file" accept="video/*" onChange={handleVideoUpload} disabled={uploadingVideo} hidden />
                </label>
              ) : (
                <span className="hint">Video limit reached ({MAX_VIDEOS}/{MAX_VIDEOS})</span>
              )}
            </>
          )}
          <LoadingButton className="danger" loading={deleting} loadingText="Deleting…" onClick={handleDelete}>
            Delete drop
          </LoadingButton>
        </div>
      )}
    </div>
  );
}
