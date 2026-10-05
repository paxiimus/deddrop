import type { Keypair } from "./crypto";

// Everything here lives only on this device — nothing is ever synced to the
// server. Losing this data means losing management access to your drops;
// that's the deliberate trade for the server holding nothing at all.

const IDENTITY_KEY = "deaddrop:identity";
const OWNER_SECRETS_KEY = "deaddrop:owner_secrets"; // drop_id -> secret
const MESSAGE_KEYS_KEY = "deaddrop:message_keys"; // drop_id -> base64 symmetric key

export const getIdentity = (): Keypair | null => JSON.parse(localStorage.getItem(IDENTITY_KEY) || "null");
export const setIdentity = (kp: Keypair) => localStorage.setItem(IDENTITY_KEY, JSON.stringify(kp));
export const clearIdentity = () => localStorage.removeItem(IDENTITY_KEY);

function getMap(key: string): Record<string, string> {
  return JSON.parse(localStorage.getItem(key) || "{}");
}
function setMap(key: string, map: Record<string, string>) {
  localStorage.setItem(key, JSON.stringify(map));
}

export const saveOwnerSecret = (dropId: string, secret: string) => {
  const m = getMap(OWNER_SECRETS_KEY);
  m[dropId] = secret;
  setMap(OWNER_SECRETS_KEY, m);
};
export const getOwnerSecret = (dropId: string): string | undefined => getMap(OWNER_SECRETS_KEY)[dropId];
// The ids of every anonymous (no-identity) drop this device holds an owner
// secret for. Previously nothing enumerated this map at all — an owner
// secret being saved per drop is only useful for management later if there's
// some way to ever see the list again, which there wasn't.
export const listOwnerSecretDropIds = (): string[] => Object.keys(getMap(OWNER_SECRETS_KEY));
/** Forget drops the server no longer has (purged at expiry), so they stop
 * being requested on every poll. Their message keys go with them. */
export const forgetDrops = (dropIds: string[]) => {
  if (!dropIds.length) return;
  for (const key of [OWNER_SECRETS_KEY, MESSAGE_KEYS_KEY]) {
    const m = getMap(key);
    for (const id of dropIds) delete m[id];
    setMap(key, m);
  }
};

export const saveMessageKey = (dropId: string, key: string) => {
  const m = getMap(MESSAGE_KEYS_KEY);
  m[dropId] = key;
  setMap(MESSAGE_KEYS_KEY, m);
};
export const getMessageKey = (dropId: string): string | undefined => getMap(MESSAGE_KEYS_KEY)[dropId];
