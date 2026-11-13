import Anthropic from "@anthropic-ai/sdk";
import { sql } from "./db";
import { getEnv } from "./env";

// Same price table as workers/gccintel/llm/pricing.py (USD per million tokens: input, output, cache read).
const CLAUDE_PRICES: Record<string, [number, number, number]> = {
  "claude-fable-5-2": [8.0, 40.0, 0.2],
  "claude-opus-5-5": [4.0, 20.0, 0.2],
  "claude-opus-5": [5.0, 25.0, 0.5],
  "claude-opus-4-8": [5.0, 25.0, 0.5],
  "claude-sonnet-5-5": [2.0, 10.0, 0.1],
  "claude-sonnet-5": [2.0, 10.0, 0.2],
  "claude-haiku-5-5": [0.1, 0.5, 0.01],
};

export function costUsd(model: string, input: number, output: number, cacheRead = 0, cacheWrite = 0): { cost: number; known: boolean } {
  const p = CLAUDE_PRICES[model];
  if (!p) return { cost: 0, known: false };
  const [i, o, c] = p;
  return { cost: Math.round(((input * i + output * o + cacheRead * c + cacheWrite * i * 1.25) / 1_000_000) * 1e6) / 1e6, known: true };
}

export type LlmOutcome =
  | { ok: true; text: string; model: string; inputTokens: number; outputTokens: number; costUsd: number; latencyMs: number; requestId: string | null }
  | { ok: false; reason: "not_configured" | "budget" | "refused" | "error"; message: string; model?: string; latencyMs?: number };

/** Spend so far today (UTC) for a tenant. Server-side check; llm_usage is admin-readable only under RLS. */
export async function spentToday(tenantId: string): Promise<number> {
  const [row] = await sql()<{ spent: string }[]>`
    select coalesce(sum(cost_usd), 0)::text as spent from llm_usage
    where tenant_id = ${tenantId} and created_at >= date_trunc('day', now())`;
  return Number(row?.spent ?? 0);
}

async function recordUsage(tenantId: string, agent: string, model: string, o: Partial<{ input: number; output: number; cacheRead: number;
  cacheWrite: number; cost: number; latency: number; success: boolean; error: string | null; requestId: string | null }>) {
  await sql()`
    insert into llm_usage(tenant_id, agent, provider, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                          cost_usd, latency_ms, success, error, request_id)
    values (${tenantId}, ${agent}, 'anthropic', ${model}, ${o.input ?? 0}, ${o.output ?? 0}, ${o.cacheRead ?? 0}, ${o.cacheWrite ?? 0},
            ${o.cost ?? 0}, ${o.latency ?? 0}, ${o.success ?? false}, ${o.error ?? null}, ${o.requestId ?? null})`;
}

/**
 * One grounded completion. Server-side fallbacks are enabled so a policy decline on the primary
 * model is retried on the server-defined fallback model; a final refusal is reported, never hidden.
 */
export async function complete(opts: {
  tenantId: string; agent: string; system: string; prompt: string; maxTokens?: number; effort?: "low" | "medium" | "high";
}): Promise<LlmOutcome> {
  const env = getEnv();
  if (!env.anthropicApiKey) {
    return { ok: false, reason: "not_configured", message: "ANTHROPIC_API_KEY is not set, so no language model is available." };
  }
  const spent = await spentToday(opts.tenantId);
  if (spent >= env.llmDailyBudgetUsd) {
    return { ok: false, reason: "budget", message: `Daily LLM budget reached ($${spent.toFixed(2)} of $${env.llmDailyBudgetUsd.toFixed(2)}).` };
  }
  const client = new Anthropic({ apiKey: env.anthropicApiKey, baseURL: env.anthropicBaseUrl, maxRetries: 2, timeout: 90_000 });
  const started = Date.now();
  try {
    const resp = await client.beta.messages.create({
      model: env.llmModel,
      max_tokens: opts.maxTokens ?? 2000,
      betas: ["server-side-fallback-2026-07-01"],
      fallbacks: "default",
      system: opts.system,
      messages: [{ role: "user", content: opts.prompt }],
      output_config: { effort: opts.effort ?? "medium" },
    });
    const latency = Date.now() - started;
    const model = resp.model || env.llmModel;
    const u = resp.usage;
    const { cost } = costUsd(model, u.input_tokens, u.output_tokens, u.cache_read_input_tokens ?? 0, u.cache_creation_input_tokens ?? 0);
    const requestId = (resp as unknown as { _request_id?: string | null })._request_id ?? null;
    if (resp.stop_reason === "refusal") {
      await recordUsage(opts.tenantId, opts.agent, model, { input: u.input_tokens, output: u.output_tokens, cost, latency, success: false,
        error: "refusal", requestId });
      return { ok: false, reason: "refused", model, latencyMs: latency,
        message: "The model declined to answer this request (after server-side fallback)." };
    }
    const text = resp.content.flatMap((b) => (b.type === "text" ? [b.text] : [])).join("").trim();
    await recordUsage(opts.tenantId, opts.agent, model, { input: u.input_tokens, output: u.output_tokens,
      cacheRead: u.cache_read_input_tokens ?? 0, cacheWrite: u.cache_creation_input_tokens ?? 0, cost, latency, success: true, requestId });
    return { ok: true, text, model, inputTokens: u.input_tokens, outputTokens: u.output_tokens, costUsd: cost, latencyMs: latency, requestId };
  } catch (err) {
    const latency = Date.now() - started;
    const message = err instanceof Anthropic.APIError ? `${err.status ?? ""} ${err.message}`.trim() : String(err);
    await recordUsage(opts.tenantId, opts.agent, env.llmModel, { latency, success: false, error: message.slice(0, 500) });
    return { ok: false, reason: "error", message: `LLM call failed: ${message.slice(0, 200)}`, latencyMs: latency };
  }
}
