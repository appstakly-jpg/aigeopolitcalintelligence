import { describe, expect, it } from "vitest";
import { describeOverrides, hasAnalystOverrides, removeAnalystOverrides, setAnalystOverrides } from "@/lib/overrides";

// A publisher correction as the worker stores it (agents/incidents.py: recompute).
const publisher = {
  location: { entity_id: "city:kw:ahmadi", name: "Ahmadi" },
  title: "Substation fire and power outage were in the Ahmadi area",
  casualties: [null, 12],
  casualty_note: "Casualty figures as corrected by the publisher.",
};

describe("incident overrides: analyst vs publisher corrections", () => {
  it("records which overrides an analyst set", () => {
    const o = setAnalystOverrides(publisher, { severity: { severity: 5, severity_reason: "grid-wide" } });
    expect(o.severity).toBe(5);
    expect(o.analyst_keys).toEqual(["severity"]);
    expect(describeOverrides(o)).toEqual([
      { key: "location", by: "publisher correction" },
      { key: "title", by: "publisher correction" },
      { key: "casualties", by: "publisher correction" },
      { key: "severity", by: "analyst" },
    ]);
    expect(hasAnalystOverrides(o)).toBe(true);
    expect(hasAnalystOverrides(publisher)).toBe(false);
  });

  it("removing analyst overrides keeps publisher corrections", () => {
    const o = removeAnalystOverrides(setAnalystOverrides(publisher, { severity: { severity: 5, severity_reason: "x" } }));
    expect(o.severity).toBeUndefined();
    expect(o.severity_reason).toBeUndefined();
    expect(o.location).toEqual(publisher.location);
    expect(o.title).toBe(publisher.title);
    expect(hasAnalystOverrides(o)).toBe(false);
  });

  it("restores the publisher value an analyst override displaced, including companion fields", () => {
    let o = setAnalystOverrides(publisher, {
      title: { title: "Analyst title" },
      casualties: { casualties: [1, 12], casualty_note: "Casualty figures set by analyst: hospital list" },
    });
    // A second edit to the same field must not overwrite the remembered publisher value.
    o = setAnalystOverrides(o, { title: { title: "Analyst title, revised" } });
    expect(o.title).toBe("Analyst title, revised");
    const restored = removeAnalystOverrides(o);
    expect(restored.title).toBe(publisher.title);
    expect(restored.casualties).toEqual(publisher.casualties);
    expect(restored.casualty_note).toBe(publisher.casualty_note);
    expect(restored.replaced_by_analyst).toEqual({});
  });

  it("removes analyst-only fields completely when nothing was displaced", () => {
    const o = removeAnalystOverrides(setAnalystOverrides(null, { categories: { categories: ["power_outage"] }, summary: { summary: "s" } }));
    expect(Object.keys(o).sort()).toEqual(["analyst_keys", "replaced_by_analyst"]);
    expect(describeOverrides(o)).toEqual([]);
  });
});
