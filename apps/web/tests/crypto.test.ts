import { describe, expect, it } from "vitest";
import { decryptSecret, encryptSecret, hashPassword, mask, verifyPassword } from "@/lib/crypto";
import { base32Encode, hotp, otpauthUri, totp, verifyTotp } from "@/lib/totp";

// Produced by workers/gccintel/security.py, so the web app and the workers can read each other's values.
const PY = {
  key: "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
  hash: "scrypt$16384$8$1$FzF25QW9/gZ/Ts9irhFsUA==$M8iaw+sKPpcHMWCwxiN1FTNFseVfc+YayflDAMTjUaLl08xJHsY4kL9VJUqBnUeOy71EmwaR9bg7CZJBOJINAw==",
  ct: "v1:mzs+BZhxTopRoHsJw+FK8f1eJRig8luKiapXJic9ITMDCKW7VJbxlWO8/A==",
  tenant: "11111111-1111-1111-1111-111111111111",
};

describe("password hashing (shared format with the Python workers)", () => {
  it("verifies a hash produced by Python", async () => {
    expect(await verifyPassword("correct horse battery staple", PY.hash)).toBe(true);
    expect(await verifyPassword("wrong password!!", PY.hash)).toBe(false);
  });
  it("round-trips its own hashes and rejects malformed input", async () => {
    const h = await hashPassword("a long enough password");
    expect(h.startsWith("scrypt$16384$8$1$")).toBe(true);
    expect(await verifyPassword("a long enough password", h)).toBe(true);
    expect(await verifyPassword("x", "not-a-hash")).toBe(false);
    expect(await verifyPassword("x", null)).toBe(false);
    await expect(hashPassword("short")).rejects.toThrow();
  });
});

describe("secret encryption (AES-256-GCM, AAD = tenant:name)", () => {
  it("decrypts a value encrypted by Python", () => {
    expect(decryptSecret(PY.ct, `${PY.tenant}:slack_webhook_url`, PY.key)).toBe("xoxb-test-value");
  });
  it("binds ciphertext to its tenant and name", () => {
    expect(() => decryptSecret(PY.ct, `22222222-2222-2222-2222-222222222222:slack_webhook_url`, PY.key)).toThrow();
    expect(() => decryptSecret(PY.ct, `${PY.tenant}:telegram_bot_token`, PY.key)).toThrow();
  });
  it("detects tampering and refuses missing keys", () => {
    const ct = encryptSecret("secret", "t:n", PY.key);
    const raw = Buffer.from(ct.slice(3), "base64");
    raw[raw.length - 1] ^= 1;
    expect(() => decryptSecret(`v1:${raw.toString("base64")}`, "t:n", PY.key)).toThrow();
    expect(() => encryptSecret("secret", "t:n", null)).toThrow(/APP_ENCRYPTION_KEY/);
    expect(decryptSecret(ct, "t:n", PY.key)).toBe("secret");
  });
  it("masks values for display", () => {
    expect(mask("analyst@example.com")).toBe("ana***@example.com");
    expect(mask("abcdef")).toBe("abc***");
  });
});

describe("TOTP (RFC 6238 SHA-1 test vectors, 6 digits)", () => {
  const secret = base32Encode(Buffer.from("12345678901234567890"));
  it("matches the published vectors", () => {
    expect(totp(secret, 59_000)).toBe("287082");
    expect(totp(secret, 1_111_111_109_000)).toBe("081804");
    expect(totp(secret, 1_234_567_890_000)).toBe("005924");
    expect(totp(secret, 2_000_000_000_000)).toBe("279037");
    expect(hotp(secret, 0)).toBe("755224");
  });
  it("accepts one step of clock drift and nothing more", () => {
    const at = 1_234_567_890_000;
    expect(verifyTotp(secret, totp(secret, at - 30_000), at)).toBe(true);
    expect(verifyTotp(secret, totp(secret, at + 30_000), at)).toBe(true);
    expect(verifyTotp(secret, totp(secret, at - 90_000), at)).toBe(false);
    expect(verifyTotp(secret, "12345", at)).toBe(false);
    expect(verifyTotp(secret, "abcdef", at)).toBe(false);
  });
  it("builds an otpauth URI", () => {
    expect(otpauthUri("ABC", "a@b.c")).toContain("otpauth://totp/GCC%20Intelligence:a%40b.c?secret=ABC");
  });
});
