import { useEffect, useState } from "react";

function useCountdown(target: string | null, onExpire?: () => void) {
  const [remaining, setRemaining] = useState("");
  useEffect(() => {
    if (!target) return;
    const targetMs = new Date(target).getTime();
    // A plain closure variable, not a ref — it only needs to persist
    // across tick() calls within this one effect run, and a fresh effect
    // run (target changed, e.g. cooling got extended) should reset it
    // fresh anyway, which redeclaring it here does for free.
    let firedExpire = false;
    let id: ReturnType<typeof setInterval>;
    const tick = () => {
      const diff = targetMs - Date.now();
      if (diff <= 0) {
        setRemaining("0:00:00");
        clearInterval(id);
        // Previously nothing here at all — the timer would sit at
        // "0:00:00" for up to 30 seconds (App.tsx's own poll interval)
        // before the app noticed cooling had actually ended and
        // transitioned back. onExpire lets the moment this countdown
        // reaches zero prompt an immediate re-check instead of waiting
        // for the next scheduled one. Guarded by firedExpire rather than
        // relying on clearInterval(id) timing alone — id can still be
        // undefined on tick()'s first, synchronous call (before the
        // setInterval below has assigned it), which would otherwise let
        // this fire twice in the edge case where cooling has already
        // expired by the time this mounts.
        if (!firedExpire) {
          firedExpire = true;
          onExpire?.();
        }
        return;
      }
      const h = Math.floor(diff / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      const s = Math.floor((diff % 60000) / 1000);
      setRemaining(`${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`);
    };
    tick();
    id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [target, onExpire]);
  return remaining;
}

export default function CoolingScreen({
  until,
  message,
  onExpire,
}: {
  until: string | null;
  message: string;
  onExpire?: () => void;
}) {
  const remaining = useCountdown(until, onExpire);
  return (
    <div className="card">
      <h2>DEADDROP COOLING</h2>
      {until && <p className="mono">{remaining}</p>}
      <p className="hint">
        {message || "More traffic than this donation-funded instance can handle right now — back shortly."}
      </p>
    </div>
  );
}
