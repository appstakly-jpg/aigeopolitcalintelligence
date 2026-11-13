// Builds an offline country basemap (Natural Earth 1:110m via world-atlas, public domain / ISC)
// so the map works without any third-party tile server.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { feature } from "topojson-client";

const require = createRequire(import.meta.url);
const topo = JSON.parse(readFileSync(require.resolve("world-atlas/countries-50m.json"), "utf8"));
const geo = feature(topo, topo.objects.countries);
// keep the Middle East / Horn of Africa / Eastern Med window to keep the file small
const inWindow = (coords) => coords.flat(3).some((v, i, a) => i % 2 === 0 && v > 5 && v < 80 && a[i + 1] > -5 && a[i + 1] < 50);
geo.features = geo.features.filter((f) => inWindow(f.geometry.coordinates)).map((f) => ({
  type: "Feature", id: f.id, properties: { name: f.properties.name }, geometry: f.geometry,
}));
mkdirSync("public/basemap", { recursive: true });
// ~1 km precision is plenty for a regional basemap and halves the file size
writeFileSync("public/basemap/countries.geojson", JSON.stringify(geo, (k, v) => (typeof v === "number" ? Math.round(v * 100) / 100 : v)));
console.log(`wrote ${geo.features.length} countries`);
