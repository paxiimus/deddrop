import { useState } from "react";

import { api } from "../lib/api";
import LoadingButton from "./LoadingButton";

export default function PrivateAccessScreen({ onGranted }: { onGranted: () => void | Promise<void> }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    if (!code) return;
    setSubmitting(true);
    setError("");
    try {
      await api.submitPrivateAccessCode(code);
      // Awaited, not fire-and-forget — onGranted is App.tsx's own
      // checkStatus, itself async. Without awaiting it here, the finally
      // block below would flip submitting back to false (reverting the
      // button's "Checking…" state) before checkStatus's own fetch had
      // actually resolved and transitioned the app away from this screen
      // — a brief, purely cosmetic flicker right before the real
      // transition, but free to avoid.
      await onGranted();
    } catch (e) {
      setError((e as Error).message || "Incorrect access code.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="card">
      <h2>Access code required</h2>
      <p className="hint">This is a private instance. Enter the access code to continue.</p>
      <input
        type="password"
        value={code}
        onChange={(e) => setCode(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit()}
        placeholder="Access code"
        autoFocus
      />
      {error && <p className="error">{error}</p>}
      <LoadingButton className="primary" loading={submitting} loadingText="Checking…" onClick={submit}>
        Continue
      </LoadingButton>
    </div>
  );
}
