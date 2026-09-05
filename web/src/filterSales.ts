export type SaleProperties = {
  ppr_id: string;
  address: string;
  sale_date: string | null;
  price: number | null;
  property_type: string | null;
  beds: number | null;
  baths: number | null;
  floor_area_m2: number | null;
  ber: string | null;
  asking_price: number | null;
  eur_per_m2: number | null;
  sale_vs_asking: number | null;
  daft_url: string | null;
  not_full_market_price: number | null;
};

export type SaleFeature = {
  type: "Feature";
  geometry: { type: "Point"; coordinates: [number, number] };
  properties: SaleProperties;
};

export type SalesGeoJSON = {
  type: "FeatureCollection";
  features: SaleFeature[];
};

export type WebSummary = {
  ppr_rows: number;
  mapped_rows: number;
  unmapped_rows: number;
  mapped_share: number;
  match_rate_exact_or_high: number;
  median_price: number | null;
  median_eur_per_m2: number | null;
  median_sale_vs_asking: number | null;
  coverage_note: string;
};

export const AREA_BANDS = [
  { id: "any", label: "Any size" },
  { id: "lt75", label: "Under 75 m²" },
  { id: "75-120", label: "75–120 m²" },
  { id: "120-180", label: "120–180 m²" },
  { id: "gt180", label: "Over 180 m²" },
] as const;

export type AreaBand = (typeof AREA_BANDS)[number]["id"];

export type SaleFilters = {
  types: string[];
  beds: number | "any";
  area: AreaBand;
};

export function uniqueTypes(features: SaleFeature[]): string[] {
  const values = new Set<string>();
  for (const feature of features) {
    const type = feature.properties.property_type?.trim() || "Unknown";
    values.add(type);
  }
  return [...values].sort((a, b) => a.localeCompare(b));
}

function matchesArea(areaM2: number | null, band: AreaBand): boolean {
  if (band === "any") return true;
  if (areaM2 == null) return false;
  if (band === "lt75") return areaM2 < 75;
  if (band === "75-120") return areaM2 >= 75 && areaM2 <= 120;
  if (band === "120-180") return areaM2 > 120 && areaM2 <= 180;
  return areaM2 > 180;
}

export function filterSales(features: SaleFeature[], filters: SaleFilters): SaleFeature[] {
  return features.filter((feature) => {
    const { property_type, beds, floor_area_m2 } = feature.properties;
    if (filters.types.length > 0) {
      const type = property_type?.trim() || "Unknown";
      if (!filters.types.includes(type)) return false;
    }
    if (filters.beds !== "any") {
      if (beds !== filters.beds) return false;
    }
    if (!matchesArea(floor_area_m2, filters.area)) return false;
    return true;
  });
}

export type FeatureStats = {
  mapped: number;
  medianPrice: number | null;
  medianEurPerM2: number | null;
};

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

// Market stats exclude not-full-market-price sales, matching summarise() in export.py.
function fullMarketValues(features: SaleFeature[], pick: (props: SaleProperties) => number | null): number[] {
  const values: number[] = [];
  for (const { properties } of features) {
    if (properties.not_full_market_price !== 0) continue;
    const value = pick(properties);
    if (value != null && Number.isFinite(value)) values.push(value);
  }
  return values;
}

export function summariseFeatures(features: SaleFeature[]): FeatureStats {
  return {
    mapped: features.length,
    medianPrice: median(fullMarketValues(features, (props) => props.price)),
    medianEurPerM2: median(fullMarketValues(features, (props) => props.eur_per_m2)),
  };
}
