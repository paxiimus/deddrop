import L from "leaflet";

export const dropIcon = L.divIcon({
  className: "drop-marker",
  html: '<span class="drop-marker-dot"></span>',
  iconSize: [16, 16],
  iconAnchor: [8, 8],
});

export const meIcon = L.divIcon({
  className: "me-marker",
  html: '<span class="me-marker-dot"></span>',
  iconSize: [14, 14],
  iconAnchor: [7, 7],
});
