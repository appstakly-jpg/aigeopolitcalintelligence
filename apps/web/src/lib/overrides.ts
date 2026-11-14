// Incident overrides (incidents.verification_detail.overrides) hold both publisher corrections, written
// by the worker, and analyst corrections, written here. Bookkeeping keys keep the two apart:
//   analyst_keys         which overrides an analyst set
//   replaced_by_analyst  publisher values an analyst override displaced, restored when it is removed
// The worker applies the same rules when a newer publisher correction arrives (agents/incidents.py).

export type Overrides = Record<string, unknown>;

const BOOKKEEPING = ["analyst_keys", "replaced_by_analyst"];
// Values stored alongside an override that are not overrides in their own right.
const COMPANIONS: Record<string, string[]> = { severity: ["severity_reason"], casualties: ["casualty_note"] };

function state(prev: Overrides | null | undefined) {
  const overrides: Overrides = { ...(prev ?? {}) };
  const analystKeys = new Set<string>(Array.isArray(overrides.analyst_keys) ? (overrides.analyst_keys as string[]) : []);
  const replaced: Record<string, Overrides> = { ...((overrides.replaced_by_analyst as Record<string, Overrides>) ?? {}) };
  return { overrides, analystKeys, replaced };
}

function finish(overrides: Overrides, analystKeys: Set<string>, replaced: Record<string, Overrides>): Overrides {
  overrides.analyst_keys = [...analystKeys];
  overrides.replaced_by_analyst = replaced;
  return overrides;
}

/** Apply analyst edits, given as { overrideKey: { storedKey: value, ... } }. */
export function setAnalystOverrides(prev: Overrides | null | undefined, edits: Record<string, Overrides>): Overrides {
  const { overrides, analystKeys, replaced } = state(prev);
  for (const [key, values] of Object.entries(edits)) {
    if (!analystKeys.has(key) && key in overrides && !(key in replaced)) {
      const fields = [key, ...(COMPANIONS[key] ?? [])].filter((k) => k in overrides);
      replaced[key] = Object.fromEntries(fields.map((k) => [k, overrides[k]]));
    }
    Object.assign(overrides, values);
    analystKeys.add(key);
  }
  return finish(overrides, analystKeys, replaced);
}

/** Remove every analyst override and restore the publisher corrections they displaced. */
export function removeAnalystOverrides(prev: Overrides | null | undefined): Overrides {
  const { overrides, analystKeys, replaced } = state(prev);
  for (const key of analystKeys) for (const k of [key, ...(COMPANIONS[key] ?? [])]) delete overrides[k];
  for (const values of Object.values(replaced)) Object.assign(overrides, values);
  return finish(overrides, new Set(), {});
}

/** Overrides in effect, with who set them, for display. */
export function describeOverrides(o: Overrides | null | undefined): { key: string; by: "analyst" | "publisher correction" }[] {
  const { overrides, analystKeys } = state(o);
  const hidden = new Set([...BOOKKEEPING, ...Object.values(COMPANIONS).flat()]);
  return Object.keys(overrides).filter((k) => !hidden.has(k))
    .map((key) => ({ key, by: analystKeys.has(key) ? "analyst" : "publisher correction" }));
}

export function hasAnalystOverrides(o: Overrides | null | undefined): boolean {
  return state(o).analystKeys.size > 0;
}
