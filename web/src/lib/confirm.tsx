import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

import Modal from "../components/Modal";

type ConfirmFn = (message: string) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn>(async () => false);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<string | null>(null);
  const resolver = useRef<(v: boolean) => void>();

  const confirmFn = useCallback<ConfirmFn>((msg) => {
    setMessage(msg);
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
    });
  }, []);

  function respond(v: boolean) {
    setMessage(null);
    resolver.current?.(v);
  }

  return (
    <ConfirmContext.Provider value={confirmFn}>
      {children}
      {message && (
        <Modal onClose={() => respond(false)}>
          <p>{message}</p>
          <div className="row">
            <button className="danger" onClick={() => respond(true)}>
              Yes, continue
            </button>
            <button onClick={() => respond(false)}>Cancel</button>
          </div>
        </Modal>
      )}
    </ConfirmContext.Provider>
  );
}

export const useConfirm = () => useContext(ConfirmContext);
