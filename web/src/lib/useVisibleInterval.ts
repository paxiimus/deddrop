import { useEffect, useRef } from "react";

/** setInterval that pauses while the tab is hidden and runs once on return.
 * A background tab otherwise keeps polling indefinitely — wasted requests,
 * battery, and per-IP rate-limit budget. */
export function useVisibleInterval(fn: () => void, ms: number) {
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    let id: ReturnType<typeof setInterval> | undefined;
    const start = () => {
      clearInterval(id);
      id = setInterval(() => fnRef.current(), ms);
    };
    const onVisibility = () => {
      if (document.hidden) {
        clearInterval(id);
      } else {
        fnRef.current();
        start();
      }
    };
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [ms]);
}
