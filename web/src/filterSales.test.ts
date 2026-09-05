import { describe, expect, it } from "vitest";
import { filterSales, summariseFeatures, type SaleFeature } from "./filterSales";

function feature(partial: Partial<SaleFeature["properties"]>): SaleFeature {
  return {
    type: "Feature",
    geometry: { type: "Point", coordinates: [-6.35, 53.72] },
    properties: {
      ppr_id: "1",
      address: "12 Barley Cove",
      sale_date: "2024-07-14",
      price: 385000,
      property_type: "Semi-D",
      beds: 4,
      baths: 3,
      floor_area_m2: 127,
      ber: "C2",
      asking_price: 375000,
      eur_per_m2: 3000,
      sale_vs_asking: 0.02,
      daft_url: "https://www.daft.ie/sold/example",
      not_full_market_price: 0,
      ...partial,
    },
  };
}

describe("filterSales", () => {
  const features = [
    feature({ property_type: "Semi-D", beds: 4, floor_area_m2: 127 }),
    feature({ ppr_id: "2", property_type: "Apartment", beds: 1, floor_area_m2: 50 }),
    feature({ ppr_id: "3", property_type: "Detached", beds: 4, floor_area_m2: 200 }),
  ];

  it("keeps all rows when filters are open", () => {
    expect(filterSales(features, { types: [], beds: "any", area: "any" })).toHaveLength(3);
  });

  it("filters by type and beds without mutating source", () => {
    const filtered = filterSales(features, { types: ["Semi-D"], beds: 4, area: "any" });
    expect(filtered.map((item) => item.properties.ppr_id)).toEqual(["1"]);
    expect(features).toHaveLength(3);
  });

  it("filters floor area bands", () => {
    const small = filterSales(features, { types: [], beds: "any", area: "lt75" });
    expect(small[0].properties.property_type).toBe("Apartment");
  });
});

describe("summariseFeatures", () => {
  const features = [
    feature({ price: 300000, eur_per_m2: 3000 }),
    feature({ ppr_id: "2", property_type: "Apartment", beds: 1, price: 200000, eur_per_m2: 4000 }),
    feature({ ppr_id: "3", price: 400000, eur_per_m2: 5000 }),
  ];

  it("returns empty stats for no rows", () => {
    expect(summariseFeatures([])).toEqual({ mapped: 0, medianPrice: null, medianEurPerM2: null });
  });

  it("medians the visible rows", () => {
    expect(summariseFeatures(features)).toEqual({ mapped: 3, medianPrice: 300000, medianEurPerM2: 4000 });
  });

  it("tracks the filtered subset", () => {
    const visible = filterSales(features, { types: ["Apartment"], beds: "any", area: "any" });
    expect(summariseFeatures(visible)).toEqual({ mapped: 1, medianPrice: 200000, medianEurPerM2: 4000 });
  });

  it("excludes not-full-market sales and missing values from medians", () => {
    const mixed = [
      feature({ price: 300000, eur_per_m2: 3000 }),
      feature({ ppr_id: "2", price: 10000, eur_per_m2: 100, not_full_market_price: 1 }),
      feature({ ppr_id: "3", price: 500000, eur_per_m2: null }),
    ];
    expect(summariseFeatures(mixed)).toEqual({ mapped: 3, medianPrice: 400000, medianEurPerM2: 3000 });
  });
});
