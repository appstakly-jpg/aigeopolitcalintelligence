// MapLibre GL 6 loads its worker from a separate module next to the main bundle. Bundlers do not
// emit that file, so copy it into public/ and point MapLibre at it with setWorkerUrl().
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);
const pkgDir = dirname(require.resolve("maplibre-gl/package.json"));
const { version } = require("maplibre-gl/package.json");
mkdirSync("public/maplibre", { recursive: true });
copyFileSync(join(pkgDir, "dist", "maplibre-gl-worker.mjs"), "public/maplibre/maplibre-gl-worker.mjs");
console.log(`copied maplibre-gl-worker.mjs (maplibre-gl ${version}) to public/maplibre/`);
