import { useEffect } from "react";
import { MapContainer, Marker, Popup, TileLayer } from "react-leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";
import L from "leaflet";
import markerIcon from "leaflet/dist/images/marker-icon.png";
import markerIcon2x from "leaflet/dist/images/marker-icon-2x.png";
import markerShadow from "leaflet/dist/images/marker-shadow.png";
import { format, parseISO } from "date-fns";
import type { SaleFeature } from "./filterSales";

// Leaflet's default icon prepends an auto-detected image path, which mangles bundled URLs.
const saleIcon = new L.Icon({
  iconUrl: markerIcon,
  iconRetinaUrl: markerIcon2x,
  shadowUrl: markerShadow,
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
});

const DROGHEDA: [number, number] = [53.717, -6.351];

function euro(value: number | null): string {
  if (value == null || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(
    value,
  );
}

function saleDate(value: string | null): string {
  if (!value) return "—";
  try {
    return format(parseISO(value.slice(0, 10)), "MMM d, yyyy");
  } catch {
    return value;
  }
}

function SalePopup({ feature }: { feature: SaleFeature }) {
  const p = feature.properties;
  const vsAsking =
    p.sale_vs_asking == null
      ? null
      : `${p.sale_vs_asking >= 0 ? "+" : ""}${(p.sale_vs_asking * 100).toFixed(1)}% vs asking`;
  return (
    <div className="min-w-[240px] space-y-2 text-sm">
      <p className="font-semibold text-zinc-950 dark:text-zinc-50">{p.address}</p>
      <p className="font-data text-zinc-500 dark:text-zinc-400">{saleDate(p.sale_date)}</p>
      <p className="font-data text-lg text-zinc-950 dark:text-zinc-50">{euro(p.price)}</p>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-zinc-500 dark:text-zinc-400">
        <dt>Type</dt>
        <dd className="text-zinc-800 dark:text-zinc-200">{p.property_type || "Unknown"}</dd>
        <dt>Beds / baths</dt>
        <dd className="text-zinc-800 dark:text-zinc-200">
          {p.beds ?? "—"} / {p.baths ?? "—"}
        </dd>
        <dt>Floor area</dt>
        <dd className="text-zinc-800 dark:text-zinc-200">{p.floor_area_m2 ? `${p.floor_area_m2} m²` : "—"}</dd>
        <dt>BER</dt>
        <dd className="text-zinc-800 dark:text-zinc-200">{p.ber || "—"}</dd>
        {p.asking_price ? (
          <>
            <dt>Asking</dt>
            <dd className="text-zinc-800 dark:text-zinc-200">{euro(p.asking_price)}</dd>
          </>
        ) : null}
        {vsAsking ? (
          <>
            <dt>Vs asking</dt>
            <dd className="text-zinc-800 dark:text-zinc-200">{vsAsking}</dd>
          </>
        ) : null}
      </dl>
      {p.daft_url ? (
        <a
          href={p.daft_url}
          target="_blank"
          rel="noopener noreferrer"
          className="daft-link inline-flex rounded-[8px] bg-[#2563eb] px-3 py-2 font-medium transition-colors hover:bg-blue-700"
        >
          Open on Daft
        </a>
      ) : (
        <p className="text-xs text-zinc-500">No Daft listing matched.</p>
      )}
    </div>
  );
}

export default function SalesMap({ features }: { features: SaleFeature[] }) {
  useEffect(() => {
    document.querySelectorAll(".leaflet-attribution a").forEach((el) => {
      el.setAttribute("rel", "noopener noreferrer");
    });
  }, []);

  return (
    <MapContainer
      center={DROGHEDA}
      zoom={13}
      className="h-[min(70vh,720px)] w-full rounded-[12px]"
      scrollWheelZoom
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <MarkerClusterGroup chunkedLoading>
        {features.map((feature) => {
          const [lng, lat] = feature.geometry.coordinates;
          return (
            <Marker key={feature.properties.ppr_id} position={[lat, lng]} icon={saleIcon}>
              <Popup>
                <SalePopup feature={feature} />
              </Popup>
            </Marker>
          );
        })}
      </MarkerClusterGroup>
    </MapContainer>
  );
}
