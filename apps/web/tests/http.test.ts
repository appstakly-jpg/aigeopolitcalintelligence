import { beforeAll, describe, expect, it } from "vitest";
import { appUrl, isSameOrigin } from "@/lib/http";

beforeAll(() => {
  process.env.DATABASE_URL = "postgres://unused";
  process.env.SESSION_SECRET = "s".repeat(32);
  process.env.APP_BASE_URL = "https://intel.example.com";
});
// Headers built directly: a fetch Request would drop the Host header.
const req = (h: Record<string, string>) => ({ headers: new Headers(h) }) as unknown as Request;

describe("same-origin check for API routes", () => {
  it("accepts the host the browser used, behind port mappings and reverse proxies", () => {
    expect(isSameOrigin(req({ origin: "http://localhost:3100", host: "localhost:3100" }))).toBe(true);
    expect(isSameOrigin(req({ origin: "https://intel.example.com", host: "web:3000", "x-forwarded-host": "intel.example.com" }))).toBe(true);
    expect(isSameOrigin(req({ origin: "https://intel.example.com", host: "web:3000" }))).toBe(true);
    expect(isSameOrigin(req({ host: "web:3000" }))).toBe(true);
  });
  it("refuses other origins", () => {
    expect(isSameOrigin(req({ origin: "https://evil.example", host: "intel.example.com" }))).toBe(false);
    expect(isSameOrigin(req({ origin: "null", host: "intel.example.com" }))).toBe(false);
  });
  it("builds links from the configured public URL", () => {
    expect(appUrl("/incidents/x")).toBe("https://intel.example.com/incidents/x");
  });
});
