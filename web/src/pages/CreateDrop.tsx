import "leaflet/dist/leaflet.css";

import { useEffect, useState } from "react";
import { MapContainer, Marker, TileLayer, useMap, useMapEvents } from "react-leaflet";
import { useNavigate, useSearchParams } from "react-router-dom";

import DropQRCode from "../components/DropQRCode";
import LoadingButton from "../components/LoadingButton";
import { api } from "../lib/api";
import { MAX_DROP_LIFETIME_HOURS, MAX_PHOTOS, MAX_VIDEOS } from "../lib/constants";
import { generateMessageKey } from "../lib/crypto";
import { dropIcon } from "../lib/mapIcons";
import { getIdentity, saveMessageKey, saveOwnerSecret } from "../lib/storage";
import { useToast } from "../lib/toast";

function LocationPicker({ onPick }: { onPick: (lat: number, lng: number) => void }) {
  useMapEvents({
    click(e) {
      onPick(e.latlng.lat, e.latlng.lng);
    },
  });
  return null;
}

// MapContainer's `center` prop only applies on first mount. Recentering must
// be an explicit, separate action (triggered only by "Use my location") —
// not tied to the same lat/lng the user might be setting by clicking or
// dragging the map, or every click would fight itself.
function Recenter({ center }: { center: [number, number] }) {
  const map = useMap();
  useEffect(() => {
    map.setView(center, 15);
  }, [center, map]);
  return null;
}

// Vite env vars are always strings — "public" is truthy in JS the same as
// any non-empty string, so this must compare against the literal expected
// value, not just check truthiness. One flag governs both the "Private
// exchange" framing below AND whether media upload is offered at all —
// see .env.example for the full reasoning.
const DEPLOYMENT_MODE = import.meta.env.VITE_DEPLOYMENT_MODE === "private" ? "private" : "public";

export default function CreateDrop() {
  const nav = useNavigate();
  const toast = useToast();
  const hasIdentity = !!getIdentity();
  // "Reuse this location" on a drop's own page links here as
  // /create?lat=..&lng=.. — pre-fills the coordinate fields for a brand new
  // drop at the same spot. Read once at mount; this page doesn't need to
  // react to the URL changing again after that.
  const [searchParams] = useSearchParams();

  const [mode, setMode] = useState<"public" | "exchange">("public");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [lat, setLat] = useState(() => searchParams.get("lat") || "");
  const [lng, setLng] = useState(() => searchParams.get("lng") || "");
  const [pasteCoords, setPasteCoords] = useState("");
  const [recenterTo, setRecenterTo] = useState<[number, number] | null>(() => {
    const la = parseFloat(searchParams.get("lat") || "");
    const lo = parseFloat(searchParams.get("lng") || "");
    return Number.isNaN(la) || Number.isNaN(lo) ? null : [la, lo];
  });
  const [password, setPassword] = useState("");
  const [isPublic, setIsPublic] = useState(true);
  const [expiresHours, setExpiresHours] = useState<number | "">("");
  const [startsHours, setStartsHours] = useState<number | "">("");
  const [asIdentity, setAsIdentity] = useState(false);
  const [photoFiles, setPhotoFiles] = useState<File[]>([]);
  const [videoFiles, setVideoFiles] = useState<File[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [locating, setLocating] = useState(false);
  const [result, setResult] = useState<{ id: string; ownerSecret?: string; shareUrl: string; photosAttached: number } | null>(
    null,
  );

  function handlePhotoSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    if (files.length > MAX_PHOTOS) {
      toast(`Only the first ${MAX_PHOTOS} photos will be used.`, "error");
    }
    setPhotoFiles(files.slice(0, MAX_PHOTOS));
  }

  function handleVideoSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    if (files.length > MAX_VIDEOS) {
      toast(`Only the first ${MAX_VIDEOS} video(s) will be used.`, "error");
    }
    setVideoFiles(files.slice(0, MAX_VIDEOS));
  }

  function locateMe() {
    if (!navigator.geolocation) {
      toast("Location isn't available in this browser — enter coordinates or click the map.", "error");
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false);
        // Full float precision from the device, not a rounded display value.
        setLat(String(pos.coords.latitude));
        setLng(String(pos.coords.longitude));
        setRecenterTo([pos.coords.latitude, pos.coords.longitude]);
      },
      () => {
        setLocating(false);
        toast("Could not get your location — enter coordinates manually or click the map.", "error");
      },
      // Precise fix (this places the drop), but bounded: no options meant no
      // timeout, so a slow GPS lock could hang indefinitely with no feedback.
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 30000 },
    );
  }

  // Accepts anything copied off a GPS device, another map, or Google Maps'
  // own "lat, lng" display — the formats precision users actually have.
  function handlePaste(value: string) {
    setPasteCoords(value);
    const match = value.match(/(-?\d+(?:\.\d+)?)[,\s]+(-?\d+(?:\.\d+)?)/);
    if (match) {
      setLat(match[1]);
      setLng(match[2]);
    }
  }

  const latNum = parseFloat(lat);
  const lngNum = parseFloat(lng);
  const hasValidCoords = lat.trim() !== "" && lng.trim() !== "" && !Number.isNaN(latNum) && !Number.isNaN(lngNum);

  async function submit() {
    if (!hasValidCoords) {
      toast("Valid coordinates are required.", "error");
      return;
    }
    setSubmitting(true);
    try {
      const body: Record<string, unknown> = { title, description, latitude: latNum, longitude: lngNum, is_public: isPublic };
      if (password) body.password = password;
      if (typeof expiresHours === "number") body.expires_hours = expiresHours;
      if (typeof startsHours === "number") body.starts_in_hours = startsHours;

      const drop = await api.createDrop(body, asIdentity && hasIdentity);
      if (drop.owner_secret) saveOwnerSecret(drop.id, drop.owner_secret);

      // Upload any selected media now, while the fresh owner_secret is
      // still in hand — attaching it here instead of forcing a separate
      // trip to the drop's own page right afterward. Each upload gets its
      // own try/catch: the drop itself already succeeded, so a single
      // upload hiccup shouldn't hide the owner_secret/share link behind an
      // unrelated failure — that would be a worse outcome than just
      // warning and letting them add it later from the drop's page.
      let photosAttached = 0;
      for (const file of photoFiles) {
        try {
          await api.uploadPhoto(drop.id, file, drop.owner_secret, asIdentity && hasIdentity);
          photosAttached++;
        } catch (e) {
          toast((e as Error).message || "A photo failed to upload — you can add it from the drop's page.", "error");
        }
      }
      for (const file of videoFiles) {
        try {
          await api.uploadVideo(drop.id, file, drop.owner_secret, asIdentity && hasIdentity);
        } catch (e) {
          toast((e as Error).message || "The video failed to upload — you can add it from the drop's page.", "error");
        }
      }

      const key = generateMessageKey();
      saveMessageKey(drop.id, key);
      setResult({
        id: drop.id,
        ownerSecret: drop.owner_secret,
        shareUrl: `${location.origin}/drop/${drop.id}#key=${key}`,
        photosAttached,
      });
    } catch (e) {
      toast((e as Error).message || "Could not create the drop — try again.", "error");
    } finally {
      setSubmitting(false);
    }
  }

  if (result) {
    return (
      <div className="card">
        <h2>{mode === "exchange" ? "Exchange ready" : "Drop created"}</h2>
        <p className="hint">
          {mode === "exchange"
            ? "Send this link to the person you're exchanging with:"
            : "Share this link with whoever should find it:"}
        </p>
        <code>{result.shareUrl}</code>
        <DropQRCode value={result.shareUrl} />
        {result.ownerSecret && (
          <>
            <p className="warning">
              Save this now — it's the only way to edit or delete this drop, and it will never be shown again. There is no
              recovery if you lose it.
            </p>
            <code>{result.ownerSecret}</code>
          </>
        )}
        <p className="hint">
          {result.photosAttached > 0 ? `${result.photosAttached} photo(s) attached. ` : ""}You can add more photos and
          message the finder from the drop's own page.
        </p>
        <button onClick={() => nav(`/drop/${result.id}`)}>View drop</button>
      </div>
    );
  }

  return (
    <div className="card">
      <h2>{mode === "exchange" ? "Arrange a private exchange" : "Create a drop"}</h2>
      <p className="hint">
        {mode === "exchange"
          ? "Fill this in, then you'll get a private link and QR code — send that to the other person. That link is the only way to reach it; it's never listed anywhere."
          : "Fill this in — everything except location is optional. You'll get a shareable link and QR code once it's created."}
      </p>

      {DEPLOYMENT_MODE === "private" && (
        <div className="form-section">
          <label className="radio-row">
            <input
              type="radio"
              name="create-mode"
              checked={mode === "public"}
              onChange={() => {
                setMode("public");
                setIsPublic(true);
              }}
            />
            Public drop — shows up in the nearby list for anyone to find
          </label>
          <label className="radio-row">
            <input
              type="radio"
              name="create-mode"
              checked={mode === "exchange"}
              onChange={() => {
                setMode("exchange");
                setIsPublic(false);
              }}
            />
            Private exchange — only reachable by the link/QR you share directly, never listed
          </label>
        </div>
      )}

      <div className="form-section">
        <h3>What</h3>
        <input
          placeholder={mode === "exchange" ? "What are you exchanging? (optional)" : "Title (optional)"}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <textarea
          placeholder={mode === "exchange" ? "Instructions for the other person (optional)" : "Description / instructions"}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>

      <div className="form-section">
        <h3>Where</h3>
        <input
          placeholder="Paste coordinates (e.g. 51.5074, -0.1278)"
          value={pasteCoords}
          onChange={(e) => handlePaste(e.target.value)}
        />
        <div className="row">
          <input
            type="text"
            inputMode="decimal"
            placeholder="Latitude (e.g. 51.50740123)"
            value={lat}
            onChange={(e) => setLat(e.target.value)}
          />
          <input
            type="text"
            inputMode="decimal"
            placeholder="Longitude (e.g. -0.12780456)"
            value={lng}
            onChange={(e) => setLng(e.target.value)}
          />
          <LoadingButton type="button" loading={locating} loadingText="Locating…" onClick={locateMe}>
            Use my location
          </LoadingButton>
        </div>
        <MapContainer center={[20, 0]} zoom={3} style={{ height: 240 }}>
          <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
          {recenterTo && <Recenter center={recenterTo} />}
          <LocationPicker
            onPick={(la, lo) => {
              setLat(String(la));
              setLng(String(lo));
            }}
          />
          {hasValidCoords && <Marker position={[latNum, lngNum]} icon={dropIcon} />}
        </MapContainer>
        <p className="hint">
          Paste or type exact coordinates for full precision — the map is a visual check, not the source of truth. Clicking
          it only sets what you clicked, screen-resolution-limited at low zoom.
        </p>
      </div>

      {DEPLOYMENT_MODE === "private" && (
        <div className="form-section">
          <h3>Media</h3>
          <label>
            Photos — optional, up to {MAX_PHOTOS}
            <input type="file" accept="image/*" multiple onChange={handlePhotoSelect} />
          </label>
          {photoFiles.length > 0 && (
            <p className="hint">
              {photoFiles.length} photo{photoFiles.length > 1 ? "s" : ""} selected — GPS/device metadata is stripped
              automatically on upload.
            </p>
          )}
          <label>
            Video — optional, up to {MAX_VIDEOS}
            <input type="file" accept="video/*" multiple onChange={handleVideoSelect} />
          </label>
          {videoFiles.length > 0 && (
            <p className="hint">
              {videoFiles.length} video{videoFiles.length > 1 ? "s" : ""} selected. Unlike photos, video isn't stripped of
              embedded location/device metadata yet — avoid this if that matters for what you're sharing.
            </p>
          )}
        </div>
      )}

      <div className="form-section">
        <h3>Access &amp; timing</h3>
        <input
          placeholder="Password (optional)"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <input
          type="number"
          placeholder={`Expires in hours — max ${MAX_DROP_LIFETIME_HOURS} (${Math.round(MAX_DROP_LIFETIME_HOURS / 24)} days); defaults to the max if left blank`}
          value={expiresHours}
          onChange={(e) => setExpiresHours(e.target.value === "" ? "" : Number(e.target.value))}
        />
        <p className="hint">
          No drop lasts longer than {Math.round(MAX_DROP_LIFETIME_HOURS / 24)} days on this instance — there's no
          "permanent" option. Everything, photos included, is deleted for good once it expires.
        </p>
        <input
          type="number"
          placeholder="Starts in hours — leave blank to start immediately"
          value={startsHours}
          onChange={(e) => setStartsHours(e.target.value === "" ? "" : Number(e.target.value))}
        />
        <p className="hint">
          {mode === "exchange"
            ? "A scheduled exchange isn't reachable at all until its start time — the link/QR just won't work yet."
            : "A scheduled drop stays out of the nearby list until its start time — a direct link still works before then, but it can't be marked collected yet."}
        </p>
        {mode === "public" && (
          <label>
            <input type="checkbox" checked={isPublic} onChange={(e) => setIsPublic(e.target.checked)} /> Show in nearby
            list
          </label>
        )}
      </div>

      {hasIdentity && (
        <div className="form-section">
          <h3>Ownership</h3>
          <label>
            <input type="checkbox" checked={asIdentity} onChange={(e) => setAsIdentity(e.target.checked)} /> Manage with my
            identity (instead of a one-time secret)
          </label>
        </div>
      )}

      <LoadingButton className="primary" loading={submitting} loadingText="Creating…" onClick={submit}>
        Create drop
      </LoadingButton>
    </div>
  );
}
