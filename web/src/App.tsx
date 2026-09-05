import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Home, MapPin, Moon, Sun, TrendingUp } from "lucide-react";
import SalesMap from "./SalesMap";
import {
  AREA_BANDS,
  filterSales,
  summariseFeatures,
  uniqueTypes,
  type AreaBand,
  type SaleFeature,
  type SalesGeoJSON,
  type WebSummary,
} from "./filterSales";

function euro(value: number | null): string {
  if (value == null) return "—";
  return new Intl.NumberFormat("en-IE", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 0,
  }).format(value);
}

function pct(value: number | null): string {
  if (value == null) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export default function App() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const [features, setFeatures] = useState<SaleFeature[]>([]);
  const [summary, setSummary] = useState<WebSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [types, setTypes] = useState<string[]>([]);
  const [beds, setBeds] = useState<number | "any">("any");
  const [area, setArea] = useState<AreaBand>("any");

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  useEffect(() => {
    Promise.all([
      fetch("/data/sales.geojson").then((res) => {
        if (!res.ok) throw new Error("Could not load sales.geojson. Run ppr-analysis geocode && ppr-analysis export-web.");
        return res.json() as Promise<SalesGeoJSON>;
      }),
      fetch("/data/summary.json").then((res) => {
        if (!res.ok) throw new Error("Could not load summary.json.");
        return res.json() as Promise<WebSummary>;
      }),
    ])
      .then(([geo, stats]) => {
        setFeatures(geo.features);
        setSummary(stats);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  const typeOptions = useMemo(() => uniqueTypes(features), [features]);
  const bedOptions = useMemo(() => {
    const values = new Set<number>();
    for (const feature of features) {
      if (feature.properties.beds != null) values.add(feature.properties.beds);
    }
    return [...values].sort((a, b) => a - b);
  }, [features]);

  const visible = useMemo(
    () => filterSales(features, { types, beds, area }),
    [features, types, beds, area],
  );

  const stats = useMemo(() => summariseFeatures(visible), [visible]);
  const filtersActive = types.length > 0 || beds !== "any" || area !== "any";
  const mappedShare = summary && summary.ppr_rows > 0 ? stats.mapped / summary.ppr_rows : null;

  function toggleType(type: string) {
    setTypes((current) => (current.includes(type) ? current.filter((item) => item !== type) : [...current, type]));
  }

  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-950 dark:bg-[#09090b] dark:text-zinc-50">
      <div className="mx-auto max-w-[1600px] space-y-6 p-6">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-4xl font-extrabold tracking-tight">Drogheda sold sales</h1>
            <p className="mt-2 max-w-3xl text-sm text-zinc-500 dark:text-zinc-400">
              Official Property Price Register prices for Drogheda (Co. Louth), mapped where an address could be
              geocoded. Listing attributes come from Daft sold cards when a match exists. PPR can contain errors; Daft
              copy is not republished. Map data © OpenStreetMap contributors.
            </p>
          </div>
          <button
            type="button"
            aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
            onClick={() => setDark((value) => !value)}
            className="rounded-[8px] border border-zinc-200 bg-white p-2 text-zinc-700 shadow-sm transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:bg-[#262626] dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            {dark ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
          </button>
        </header>

        {error ? (
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-amber-800 dark:border-amber-800/30 dark:bg-amber-900/20 dark:text-amber-300">
            {error}
          </div>
        ) : null}

        <section className="grid grid-cols-1 gap-4 md:grid-cols-4">
          <Kpi
            icon={<Home className="h-4 w-4" />}
            label="PPR sales"
            value={summary ? String(summary.ppr_rows) : "—"}
            hint="All Drogheda rows, unfiltered"
          />
          <Kpi
            icon={<MapPin className="h-4 w-4" />}
            label="On the map"
            value={summary ? String(stats.mapped) : "—"}
            hint={mappedShare == null ? undefined : `${pct(mappedShare)} of PPR sales`}
          />
          <Kpi
            icon={<TrendingUp className="h-4 w-4" />}
            label="Median price"
            value={euro(stats.medianPrice)}
            hint="Full-market sales on the map"
          />
          <Kpi label="Median €/m²" value={euro(stats.medianEurPerM2)} hint="Full-market sales on the map" />
        </section>

        <section className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm dark:border-zinc-800 dark:bg-[#0c0c0f]">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-zinc-500">Filters</h2>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Showing {visible.length} of {features.length} mapped sales
            {filtersActive ? "; the map and the stats above follow these filters" : ""}. Source data is unchanged.
          </p>
          <div className="mt-4 grid gap-4 md:grid-cols-3">
            <fieldset>
              <legend className="mb-1.5 text-xs font-medium text-zinc-500">House type</legend>
              <div className="flex flex-wrap gap-2">
                {typeOptions.map((type) => {
                  const active = types.includes(type);
                  return (
                    <button
                      key={type}
                      type="button"
                      onClick={() => toggleType(type)}
                      className={`rounded-[8px] border px-3 py-1.5 text-sm font-medium transition-colors ${
                        active
                          ? "border-blue-500 bg-white text-[#2563eb] shadow-sm dark:bg-zinc-800 dark:text-[#60a5fa]"
                          : "border-zinc-200 bg-zinc-100 text-zinc-600 hover:text-zinc-900 dark:border-zinc-800 dark:bg-[#0c0c0f] dark:text-zinc-400"
                      }`}
                    >
                      {type}
                    </button>
                  );
                })}
              </div>
            </fieldset>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-zinc-500">Bedrooms</span>
              <select
                value={beds === "any" ? "any" : String(beds)}
                onChange={(event) => setBeds(event.target.value === "any" ? "any" : Number(event.target.value))}
                className="w-full rounded-[8px] border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-950 shadow-sm transition-all focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 dark:border-zinc-800 dark:bg-[#09090b] dark:text-zinc-100"
              >
                <option value="any">Any</option>
                {bedOptions.map((count) => (
                  <option key={count} value={count}>
                    {count} bed{count === 1 ? "" : "s"}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-zinc-500">Floor area</span>
              <select
                value={area}
                onChange={(event) => setArea(event.target.value as AreaBand)}
                className="w-full rounded-[8px] border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-950 shadow-sm transition-all focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 dark:border-zinc-800 dark:bg-[#09090b] dark:text-zinc-100"
              >
                {AREA_BANDS.map((band) => (
                  <option key={band.id} value={band.id}>
                    {band.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </section>

        <section className="overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-[#0c0c0f]">
          {features.length === 0 && !error ? (
            <div className="h-[min(70vh,720px)] animate-pulse rounded-[12px] bg-zinc-200 dark:bg-zinc-800" />
          ) : (
            <SalesMap features={visible} />
          )}
        </section>
      </div>
    </div>
  );
}

function Kpi({
  label,
  value,
  hint,
  icon,
}: {
  label: string;
  value: string;
  hint?: string;
  icon?: ReactNode;
}) {
  return (
    <article className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm dark:border-zinc-800 dark:bg-[#0c0c0f]">
      <div className="flex items-center gap-2 text-zinc-500 dark:text-zinc-400">
        {icon}
        <p className="text-sm">{label}</p>
      </div>
      <p className="mt-2 font-data text-3xl font-medium text-zinc-950 dark:text-zinc-50">{value}</p>
      {hint ? <p className="mt-1 text-xs text-zinc-500">{hint}</p> : null}
    </article>
  );
}
