import nacl from "tweetnacl";
import { decodeBase64, encodeBase64 } from "tweetnacl-util";

export interface Keypair {
  publicKey: string; // hex
  secretKey: string; // hex
}

const toHex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
const fromHex = (hex: string) => {
  // TypeScript's non-null assertion on .match() is a compile-time-only
  // promise, erased at runtime — it doesn't stop an actual empty or
  // malformed string from reaching here and crashing on `null.map(...)`
  // with a confusing "Cannot read properties of null" instead of a clear
  // error. A malformed identity (e.g. imported from a corrupted backup
  // file — Identity.tsx's import validation only checks that secretKey is
  // *a string*, not that it's valid hex) could plausibly reach this.
  const pairs = hex.match(/.{1,2}/g);
  if (!pairs) throw new Error("Invalid hex string.");
  return new Uint8Array(pairs.map((b) => parseInt(b, 16)));
};

export function generateKeypair(): Keypair {
  const kp = nacl.sign.keyPair();
  return { publicKey: toHex(kp.publicKey), secretKey: toHex(kp.secretKey) };
}

/** Must exactly match what the server signs against — see
 * identities/auth.py: f"{method}|{path}|{timestamp}|{body_sha256_hex}".
 * `path` must be the exact request path with NO query string (Django's
 * request.path drops it). `bodyHash` must be the SHA-256 hex digest of the
 * *exact* bytes sent as the request body — signing only method+path+
 * timestamp would let anyone who observes one valid signed request replay
 * those same headers with a completely different body. */
export function signRequest(secretKeyHex: string, method: string, path: string, timestamp: number, bodyHash: string): string {
  const message = new TextEncoder().encode(`${method}|${path}|${timestamp}|${bodyHash}`);
  const sig = nacl.sign.detached(message, fromHex(secretKeyHex));
  return toHex(sig);
}

/** SHA-256 hex digest of a string, encoded as UTF-8 — same convention the
 * server uses on the raw request body. Empty string is the fixed sentinel
 * for bodyless requests (GET/DELETE) and for multipart uploads, which can't
 * practically be hashed client-side (see api.ts). */
export async function sha256Hex(data: string): Promise<string> {
  const bytes = new TextEncoder().encode(data);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

// --- Message E2E encryption ---------------------------------------------
// The server only ever stores/relays opaque ciphertext. The symmetric key
// lives in the URL fragment (`#key=...`), which browsers never send in an
// HTTP request — the same trick PrivateBin and Firefox Send use. Whoever has
// the full link can read the thread; the server never can.

export function generateMessageKey(): string {
  return encodeBase64(nacl.randomBytes(nacl.secretbox.keyLength));
}

export function encryptMessage(plaintext: string, keyB64: string): string {
  const key = decodeBase64(keyB64);
  const nonce = nacl.randomBytes(nacl.secretbox.nonceLength);
  const box = nacl.secretbox(new TextEncoder().encode(plaintext), nonce, key);
  return `${encodeBase64(nonce)}:${encodeBase64(box)}`;
}

export function decryptMessage(ciphertext: string, keyB64: string): string {
  const [nonceB64, boxB64] = ciphertext.split(":");
  const opened = nacl.secretbox.open(decodeBase64(boxB64), decodeBase64(nonceB64), decodeBase64(keyB64));
  if (!opened) throw new Error("Failed to decrypt — wrong key?");
  return new TextDecoder().decode(opened);
}
