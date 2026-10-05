import { useEffect, useState } from "react";

import { api } from "../lib/api";
import DropSummary from "../components/DropSummary";
import LoadingButton from "../components/LoadingButton";
import Spinner from "../components/Spinner";
import { useConfirm } from "../lib/confirm";
import { generateKeypair, type Keypair } from "../lib/crypto";
import { clearIdentity, forgetDrops, getIdentity, listOwnerSecretDropIds, setIdentity } from "../lib/storage";
import { useToast } from "../lib/toast";
import { useVisibleInterval } from "../lib/useVisibleInterval";

interface DropListItem {
  id: string;
  title: string;
  status: "active" | "expired" | "collected";
}

// Defined at module scope, not inside Identity() — a component defined
// inside another component's body gets recreated on every render, which
// React treats as a brand-new component type each time and remounts.
function DropList({ items }: { items: DropListItem[] | null }) {
  if (items === null) {
    return (
      <p className="loading-line">
        <Spinner /> Loading…
      </p>
    );
  }
  if (items.length === 0) return <p>None yet.</p>;
  return (
    <ul className="drop-list">
      {items.map((d) => (
        <li key={d.id}>
          <DropSummary id={d.id} title={d.title} status={d.status} />
        </li>
      ))}
    </ul>
  );
}

const POLL_INTERVAL_MS = 60000;

export default function Identity() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const [identity, setLocal] = useState<Keypair | null>(getIdentity());
  const [identityDrops, setIdentityDrops] = useState<DropListItem[] | null>(null);
  const [anonDrops, setAnonDrops] = useState<DropListItem[] | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [savingName, setSavingName] = useState(false);

  function fetchIdentityDrops() {
    if (!identity) {
      setIdentityDrops(null);
      return;
    }
    api.myDrops().then(setIdentityDrops).catch(() => setIdentityDrops([]));
  }

  function fetchAnonDrops() {
    // Anonymous drops have no server-side "mine" concept — the only record
    // of "you created this" is the owner secret sitting in this device's
    // own storage. Without enumerating those keys, there was previously no
    // way to ever see a list of them again, even though the data was right
    // there in localStorage the whole time.
    const ids = listOwnerSecretDropIds().slice(0, 50);
    if (ids.length === 0) {
      setAnonDrops([]);
      return;
    }
    // One batched request. Previously one request per drop every 30s, which
    // with a few drops exceeded the API's 300/hour read limit on its own.
    api
      .dropsByIds(ids)
      .then((found: DropListItem[]) => {
        setAnonDrops(found);
        // Only on a successful response: anything missing has been purged.
        const live = new Set(found.map((d) => d.id));
        forgetDrops(ids.filter((id) => !live.has(id)));
      })
      .catch(() => setAnonDrops((prev) => prev ?? []));
  }

  useEffect(() => {
    fetchIdentityDrops();
    if (identity) {
      api
        .getMe()
        .then((me: { display_name: string }) => setDisplayName(me.display_name || ""))
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity]);

  useEffect(() => {
    fetchAnonDrops();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // No push notifications — this page refreshes itself while open and
  // visible, so a drop being collected or expiring shows up without a
  // reload. The hook always calls the latest closure, so identity changes
  // are picked up without re-creating the timer.
  useVisibleInterval(() => {
    fetchIdentityDrops();
    fetchAnonDrops();
  }, POLL_INTERVAL_MS);

  function create() {
    const kp = generateKeypair();
    setIdentity(kp);
    setLocal(kp);
    toast("Identity generated on this device.", "success");
  }

  async function wipe() {
    const ok = await confirmDialog(
      "This deletes your identity from this device. Any drops managed by it become unmanageable unless you exported the key first. Continue?",
    );
    if (!ok) return;
    clearIdentity();
    setLocal(null);
    setIdentityDrops(null);
    toast("Identity removed from this device.");
  }

  function exportKey() {
    if (!identity) return;
    const blob = new Blob([JSON.stringify(identity)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "deaddrop-identity.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000); // give the download a moment to actually start
    toast("Downloaded. Keep this file somewhere safe.");
  }

  async function importKey(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      // Not just typeof === "string" — an empty string passes that check
      // too, and would previously have gone on to crash confusingly the
      // first time it was actually used to sign something (fromHex in
      // crypto.ts). Ed25519 keys have fixed, known lengths — validating
      // the actual hex format and length here catches a corrupted or
      // malicious backup file at the point it's imported, with a clear
      // message, instead of letting it in and failing unpredictably later.
      const isHex = (v: unknown, expectedLength: number) => typeof v === "string" && new RegExp(`^[0-9a-f]{${expectedLength}}$`, "i").test(v);
      if (!isHex(parsed?.publicKey, 64) || !isHex(parsed?.secretKey, 128)) {
        throw new Error("Not a valid identity file.");
      }
      // The server only accepts canonical lowercase hex keys.
      parsed.publicKey = parsed.publicKey.toLowerCase();
      parsed.secretKey = parsed.secretKey.toLowerCase();
      if (identity) {
        const ok = await confirmDialog("This replaces your current identity on this device. Continue?");
        if (!ok) return;
      }
      setIdentity(parsed);
      setLocal(parsed);
      toast("Identity imported.", "success");
    } catch {
      toast("Could not read that file as a Dead Drop identity.", "error");
    }
  }

  async function saveDisplayName() {
    setSavingName(true);
    try {
      await api.updateMe(displayName);
      toast("Display name saved.", "success");
    } catch (e) {
      toast((e as Error).message || "Could not save that.", "error");
    } finally {
      setSavingName(false);
    }
  }

  return (
    <>
      <p className="hint">
        Every drop gets an owner one of two ways: anonymously, via a one-time secret — shown once when you create it,
        remembered by this device automatically, listed under "Anonymous drops" below — or through an identity, one
        reusable keypair for managing many drops without saving a separate secret for each. Neither is required.
      </p>

      {identity ? (
        <div className="card">
          <h2>Your identity</h2>
          <p className="mono">{identity.publicKey}</p>

          <label>
            Display name (optional, only ever shown to you)
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="No name set" />
          </label>
          <LoadingButton className="primary" loading={savingName} loadingText="Saving…" onClick={saveDisplayName}>
            Save name
          </LoadingButton>

          <button onClick={exportKey}>Export (back up)</button>
          <label className="file-label">
            Import a backup
            <input type="file" accept="application/json" onChange={importKey} hidden />
          </label>
          <button className="danger" onClick={wipe}>
            Delete from this device
          </button>

          <h3>Drops managed by this identity</h3>
          <DropList items={identityDrops} />
        </div>
      ) : (
        <div className="card">
          <h2>Identity (optional)</h2>
          <p className="hint">
            Nothing but a keypair generated right here on this device — the server never sees the private half. Skip
            it entirely if the one-time-secret default above is enough for you.
          </p>
          <button className="primary" onClick={create}>Generate an identity</button>
          <label className="file-label">
            Import a backup
            <input type="file" accept="application/json" onChange={importKey} hidden />
          </label>
        </div>
      )}

      <div className="card">
        <h2>Anonymous drops on this device</h2>
        <p className="hint">Every drop with a one-time secret saved on this device shows up here automatically.</p>
        <p className="hint">
          There's no push notification system — instead, this page checks for status changes (found, expired) every 30
          seconds while it stays open in a tab.
        </p>
        <DropList items={anonDrops} />
      </div>
    </>
  );
}
