// Server-side configuration. Read lazily so `next build` does not need production secrets.

export type AuthProvider = "local" | "supabase";

function bool(v: string | undefined, d = false): boolean {
  if (v === undefined || v === "") return d;
  return ["1", "true", "yes", "on"].includes(v.trim().toLowerCase());
}

export function getEnv() {
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) throw new Error("DATABASE_URL is not set");
  const sessionSecret = process.env.SESSION_SECRET ?? "";
  if (sessionSecret.length < 32) throw new Error("SESSION_SECRET must be set to at least 32 random characters");
  const authProvider = (process.env.AUTH_PROVIDER ?? "local") as AuthProvider;
  if (authProvider !== "local" && authProvider !== "supabase") throw new Error("AUTH_PROVIDER must be local or supabase");
  return {
    databaseUrl,
    sessionSecret,
    authProvider,
    supabaseJwtSecret: process.env.SUPABASE_JWT_SECRET || null,
    encryptionKey: process.env.APP_ENCRYPTION_KEY || null,
    demoMode: bool(process.env.DEMO_MODE),
    anthropicApiKey: process.env.ANTHROPIC_API_KEY || null,
    // Explicit base URL so an ambient ANTHROPIC_BASE_URL (e.g. a platform proxy) is never picked up silently.
    anthropicBaseUrl: process.env.LLM_ANTHROPIC_BASE_URL || "https://api.anthropic.com",
    llmModel: process.env.LLM_MODEL_CHAT || process.env.LLM_MODEL || "claude-opus-5-5",
    llmDailyBudgetUsd: Number(process.env.LLM_DAILY_BUDGET_USD || "10"),
    secureCookies: bool(process.env.SECURE_COOKIES, process.env.NODE_ENV === "production"),
    appBaseUrl: process.env.APP_BASE_URL || "http://localhost:3000",
  };
}

export type Env = ReturnType<typeof getEnv>;
