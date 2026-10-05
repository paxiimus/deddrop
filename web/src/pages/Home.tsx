import "leaflet/dist/leaflet.css";

import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, Marker, Popup, TileLayer, useMap, useMapEvents } from "react-leaflet";
import { Link } from "react-router-dom";

import { api } from "../lib/api";
import DropSummary from "../components/DropSummary";
import LoadingButton from "../components/LoadingButton";
import Spinner from "../components/Spinner";
import { dropIcon, meIcon } from "../lib/mapIcons";

interface DropListItem {
  id: string;
  title: string;
  latitude: number | null;
  longitude: number | null;
  is_password_protected: boolean;
  distance_m: number | null;
  created_at: string;
  expires_at: string | null;
}

type SortBy = "distance" | "newest" | "expiring";

// Geocoding goes to OpenStreetMap's Nominatim — the same operator already
// serving the map tiles, so no new party learns where you're looking. Its
// usage policy forbids autocomplete, so this only runs on submit.
const GEOCODER = "https://nominatim.openstreetmap.org/search";
// Coarse, recently-cached positions are fine for a 50km browse radius. The
// previous defaults (no timeout, maximumAge 0) made the browser wait for a
// fresh precise fix every visit — the ~20s delay.
const GEO_OPTIONS: PositionOptions = { enableHighAccuracy: false, timeout: 10000, maximumAge: 10 * 60 * 1000 };
const VIEW_CACHE_MS = 5 * 60 * 1000;

// In memory only, never persisted: returning to this page within a few
// minutes reuses the last view instead of locating + fetching again.
let lastView: { center: [number, number]; drops: DropListItem[]; at: number } | null = null;

function parseLatLng(q: string): [number, number] | null {
  const m = q.trim().match(/^(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)$/);
  if (!m) return null;
  const lat = parseFloat(m[1]);
  const lng = parseFloat(m[2]);
  return Math.abs(lat) <= 90 && Math.abs(lng) <= 180 ? [lat, lng] : null;
}

// MapContainer's `center` only applies on first render; this moves the map
// whenever the searched location changes, and reports user pans.
function MapController({ center, onMoved }: { center: [number, number]; onMoved: (c: [number, number]) => void }) {
  const map = useMap();
  useEffect(() => {
    map.setView(center, map.getZoom());
  }, [map, center]);
  useMapEvents({
    moveend: () => {
      const c = map.getCenter();
      onMoved([c.lat, c.lng]);
    },
  });
  return null;
}

export default function Home() {
  const cached = lastView && Date.now() - lastView.at < VIEW_CACHE_MS ? lastView : null;
  const [drops, setDrops] = useState<DropListItem[] | null>(cached?.drops ?? null);
  const [me, setMe] = useState<[number, number] | null>(cached?.center ?? null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"" | "locating" | "searching" | "loading">("");
  const [panned, setPanned] = useState<[number, number] | null>(null);
  const [sortBy, setSortBy] = useState<SortBy>("distance");
  const [hideLocked, setHideLocked] = useState(false);
  // Ignore results from superseded requests (e.g. a slow geolocation that
  // resolves after the user has already searched somewhere else).
  const requestId = useRef(0);

  useEffect(() => {
    if (!cached) locateMe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function locateMe() {
    if (!navigator.geolocation) {
      setError("Location isn't available in this browser — search for a place instead.");
      return;
    }
    const id = ++requestId.current;
    setStatus("locating");
    setError("");
    navigator.geolocation.getCurrentPosition(
      (pos) => id === requestId.current && load(pos.coords.latitude, pos.coords.longitude, id),
      (err) => {
        if (id !== requestId.current) return;
        setStatus("");
        setError(
          err.code === err.PERMISSION_DENIED
            ? "Location access denied — search for a place instead."
            : "Couldn't get your location — search for a place instead.",
        );
      },
      GEO_OPTIONS,
    );
  }

  async function load(lat: number, lng: number, id = ++requestId.current) {
    setStatus("loading");
    setError("");
    setMe([lat, lng]);
    setPanned(null);
    try {
      const result = await api.listDrops(lat, lng);
      if (id !== requestId.current) return;
      setDrops(result);
      lastView = { center: [lat, lng], drops: result, at: Date.now() };
    } catch (e) {
      if (id === requestId.current) setError((e as Error).message);
    } finally {
      if (id === requestId.current) setStatus("");
    }
  }

  async function search(e: FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    const coords = parseLatLng(q);
    if (coords) return load(coords[0], coords[1]);
    const id = ++requestId.current;
    setStatus("searching");
    setError("");
    try {
      const res = await fetch(`${GEOCODER}?format=jsonv2&limit=1&q=${encodeURIComponent(q)}`);
      const hits: { lat: string; lon: string }[] = res.ok ? await res.json() : [];
      if (id !== requestId.current) return;
      if (!hits.length) {
        setStatus("");
        setError(`No place found for "${q}".`);
        return;
      }
      load(parseFloat(hits[0].lat), parseFloat(hits[0].lon), id);
    } catch {
      if (id !== requestId.current) return;
      setStatus("");
      setError("Place search is unavailable right now — try coordinates (lat, lng).");
    }
  }

  const busy = status !== "";
  const searchBar = (
    <form className="row" onSubmit={search}>
      <input
        type="search"
        placeholder="Search a place, or lat, lng"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        aria-label="Search location"
      />
      <LoadingButton className="primary" loading={status === "searching"} loadingText="Searching…" type="submit">
        Search
      </LoadingButton>
      <LoadingButton loading={status === "locating"} loadingText="Locating…" onClick={locateMe} type="button">
        📍 Near me
      </LoadingButton>
    </form>
  );

  // Sorted/filtered client-side — the whole result set is already in memory
  // (the API caps it at 500 within a 50km radius). Memoised: up to 500
  // date-parsing comparisons shouldn't rerun on every status change.
  const visibleDrops = useMemo(
    () =>
      (drops ?? [])
        .filter((d) => !hideLocked || !d.is_password_protected)
        .sort((a, b) => {
          if (sortBy === "distance") return (a.distance_m ?? Infinity) - (b.distance_m ?? Infinity);
          if (sortBy === "newest") return Date.parse(b.created_at) - Date.parse(a.created_at);
          return Date.parse(a.expires_at ?? "") - Date.parse(b.expires_at ?? "") || 0;
        }),
    [drops, hideLocked, sortBy],
  );
  const onMapMoved = useCallback((c: [number, number]) => setPanned(c), []);

  if (!drops || !me) {
    return (
      <>
        {searchBar}
        {error ? (
          <p className="error">{error}</p>
        ) : (
          <p className="loading-line">
            <Spinner /> {status === "searching" ? "Finding that place…" : status === "loading" ? "Loading drops…" : "Locating you…"}
          </p>
        )}
      </>
    );
  }

  return (
    <>
      {searchBar}
      {error && <p className="error">{error}</p>}
      {busy && (
        <p className="loading-line">
          <Spinner /> {status === "locating" ? "Locating you…" : status === "searching" ? "Finding that place…" : "Updating…"}
        </p>
      )}
      <p className="hint">
        Public drops in this area — anyone can find these. Locked ones (🔒) show up here but hide their exact location until
        you open them and enter the password.
      </p>

      <MapContainer center={me} zoom={14} style={{ height: 320, marginBottom: 12 }}>
        <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        <MapController center={me} onMoved={onMapMoved} />
        <Marker position={me} icon={meIcon} />
        {visibleDrops
          .filter((d) => d.latitude != null && d.longitude != null)
          .map((d) => (
            <Marker key={d.id} position={[d.latitude as number, d.longitude as number]} icon={dropIcon}>
              <Popup>
                <strong>{d.title || "Untitled drop"}</strong>
                {d.is_password_protected && " 🔒"}
                <br />
                <Link to={`/drop/${d.id}`}>View drop</Link>
              </Popup>
            </Marker>
          ))}
      </MapContainer>
      {panned && Math.hypot(panned[0] - me[0], panned[1] - me[1]) > 0.01 && (
        <LoadingButton loading={status === "loading"} loadingText="Loading…" onClick={() => load(panned[0], panned[1])}>
          Search this area
        </LoadingButton>
      )}

      {drops.length > 0 && (
        <div className="row">
          <label>
            Sort by
            <select value={sortBy} onChange={(e) => setSortBy(e.target.value as SortBy)}>
              <option value="distance">Closest first</option>
              <option value="newest">Newest first</option>
              <option value="expiring">Expiring soonest</option>
            </select>
          </label>
          <label>
            <input type="checkbox" checked={hideLocked} onChange={(e) => setHideLocked(e.target.checked)} /> Hide locked
            drops
          </label>
        </div>
      )}

      {drops.length === 0 ? (
        <p>
          No public drops in this area yet. <Link to="/create">Create one</Link>.
        </p>
      ) : visibleDrops.length === 0 ? (
        <p className="hint">All nearby drops are locked — turn off "Hide locked drops" to see them.</p>
      ) : (
        <ul className="drop-list">
          {visibleDrops.map((d) => (
            <li key={d.id}>
              <DropSummary id={d.id} title={d.title} isPasswordProtected={d.is_password_protected} distanceM={d.distance_m} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
