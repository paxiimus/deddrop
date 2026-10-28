import "leaflet/dist/leaflet.css";
import { MapContainer, Marker, TileLayer } from "react-leaflet";

import { dropIcon } from "../lib/mapIcons";

// On touch screens a draggable map in a scrolling page traps the finger, so
// it's pan-locked there; zoom buttons still work, and "Navigate" is right below.
const coarse = typeof matchMedia === "function" && matchMedia("(pointer: coarse)").matches;

export default function DropMap({ lat, lng }: { lat: number; lng: number }) {
  return (
    <MapContainer
      key={`${lat},${lng}`}
      center={[lat, lng]}
      zoom={16}
      className="drop-map"
      scrollWheelZoom={false}
      dragging={!coarse}
    >
      <TileLayer attribution="&copy; OpenStreetMap contributors" url="/tiles/{z}/{x}/{y}.png" maxZoom={19} />
      <Marker position={[lat, lng]} icon={dropIcon} />
    </MapContainer>
  );
}
