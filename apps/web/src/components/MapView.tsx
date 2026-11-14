"use client";

import { AttributionControl, Map as MlMap, type MapLayerMouseEvent, NavigationControl, Popup, setWorkerUrl, type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";

export interface MapPoint {
  id: string;
  ref: string;
  title: string;
  lat: number;
  lon: number;
  severity: number;
  verification: string;
  precision: string;
  geoConfidence: number | null;
  location: string | null;
  isDemo: boolean;
}

const GCC_NAMES = ["Saudi Arabia", "United Arab Emirates", "Qatar", "Kuwait", "Bahrain", "Oman"];
const SEV_COLORS = ["#94a3b8", "#94a3b8", "#0ea5e9", "#f59e0b", "#f97316", "#dc2626"];

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

function localStyle(dark: boolean): StyleSpecification {
  return {
    version: 8,
    sources: { countries: { type: "geojson", data: `${window.location.origin}/basemap/countries.geojson` } },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": dark ? "#0b1220" : "#dbe7f0" } },
      {
        id: "land", type: "fill", source: "countries",
        paint: {
          "fill-color": ["case", ["in", ["get", "name"], ["literal", GCC_NAMES]], dark ? "#1f3b3a" : "#e7f3ef", dark ? "#1e2533" : "#f6f6f4"],
        },
      },
      { id: "borders", type: "line", source: "countries", paint: { "line-color": dark ? "#475569" : "#94a3b8", "line-width": 0.6 } },
    ],
  };
}

/**
 * Incident map. Only incidents with stored coordinates are drawn; the circle radius reflects geographic
 * precision (a country centroid is drawn as a large, faint area, never as a precise point).
 */
export function MapView({ points, height = 420, center = [50.5, 23.5], zoom = 3.6, unlocated = 0 }:
  { points: MapPoint[]; height?: number; center?: [number, number]; zoom?: number; unlocated?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [cx, cy] = center;
  useEffect(() => {
    if (!ref.current) return;
    // The worker module is served from /public (see scripts/copy-maplibre-worker.mjs).
    setWorkerUrl(`${window.location.origin}/maplibre/maplibre-gl-worker.mjs`);
    const dark = document.documentElement.classList.contains("dark");
    const styleUrl = process.env.NEXT_PUBLIC_MAP_STYLE_URL;
    const map = new MlMap({
      container: ref.current, style: styleUrl || localStyle(dark), center: [cx, cy], zoom, attributionControl: false, cooperativeGestures: false,
    });
    map.addControl(new NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new AttributionControl({
      compact: true, customAttribution: styleUrl ? undefined : "Boundaries: Natural Earth via world-atlas (public domain)",
    }));
    map.on("load", () => {
      map.addSource("incidents", {
        type: "geojson",
        data: {
          type: "FeatureCollection",
          features: points.map((p) => ({
            type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
            properties: { ...p, color: SEV_COLORS[p.severity] ?? SEV_COLORS[1], coarse: ["country", "admin1", "maritime_zone", "corridor"].includes(p.precision) },
          })),
        },
      });
      map.addLayer({
        id: "incidents", type: "circle", source: "incidents",
        paint: {
          "circle-color": ["get", "color"],
          "circle-radius": ["case", ["get", "coarse"], 16, 7],
          "circle-opacity": ["case", ["get", "coarse"], 0.25, 0.85],
          "circle-stroke-width": ["case", ["get", "coarse"], 1, 1.5],
          "circle-stroke-color": ["get", "color"],
        },
      });
      map.on("click", "incidents", (e: MapLayerMouseEvent) => {
        const f = e.features?.[0];
        if (!f) return;
        const p = f.properties as Record<string, string | number | boolean | null>;
        const conf = p.geoConfidence === null || p.geoConfidence === undefined || p.geoConfidence === "null" ? "n/a" : `${Math.round(Number(p.geoConfidence) * 100)}%`;
        new Popup({ maxWidth: "300px" })
          .setLngLat((f.geometry as GeoJSON.Point).coordinates as [number, number])
          .setHTML(
            `<div><a href="/incidents/${escapeHtml(String(p.id))}" style="font-weight:600">${escapeHtml(String(p.ref))}: ${escapeHtml(String(p.title))}</a>` +
            `<div style="margin-top:4px">Severity ${escapeHtml(String(p.severity))}/5 · ${escapeHtml(String(p.verification).replace("_", " "))}</div>` +
            `<div>Location: ${escapeHtml(String(p.location ?? "unknown"))}</div>` +
            `<div>Precision: ${escapeHtml(String(p.precision))} · geo-confidence ${conf}</div>` +
            (p.isDemo ? `<div style="color:#7c3aed;margin-top:4px">SYNTHETIC demo record</div>` : "") +
            `<div style="margin-top:4px"><a href="/incidents/${escapeHtml(String(p.id))}">View evidence →</a></div></div>`,
          )
          .addTo(map);
      });
      map.on("mouseenter", "incidents", () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "incidents", () => { map.getCanvas().style.cursor = ""; });
    });
    return () => map.remove();
  }, [points, cx, cy, zoom]);

  return (
    <div className="relative overflow-hidden rounded-lg border">
      <div ref={ref} style={{ height }} className="w-full" role="region" aria-label="Incident map" />
      <div className="pointer-events-none absolute bottom-2 start-2 rounded bg-card/90 px-2 py-1 text-[11px] text-muted-foreground shadow">
        {points.length} located incident(s){unlocated ? ` · ${unlocated} without a resolvable location (listed, not mapped)` : ""} · large faint circles = coarse precision
      </div>
    </div>
  );
}
