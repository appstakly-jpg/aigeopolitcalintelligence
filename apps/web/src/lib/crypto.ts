// Formats shared with workers/gccintel/security.py:
//   password: "scrypt$16384$8$1$<salt b64>$<key b64>" (64-byte key)
//   secret:   "v1:<base64(nonce12 || ciphertext || tag16)>", AAD = "<tenant_id>:<name>"
import { createCipheriv, createDecipheriv, randomBytes, scrypt as scryptCb, timingSafeEqual } from "node:crypto";

function scrypt(password: string, salt: Buffer, keylen: number, opts: { N: number; r: number; p: number }): Promise<Buffer> {
  return new Promise((resolve, reject) =>
    scryptCb(password, salt, keylen, { ...opts, maxmem: 128 * opts.N * opts.r * 2 }, (err, key) =>
      err ? reject(err) : resolve(key),
    ),
  );
}

export async function hashPassword(password: string): Promise<string> {
  if (password.length < 10) throw new Error("password must be at least 10 characters");
  const salt = randomBytes(16);
  const key = await scrypt(password, salt, 64, { N: 16384, r: 8, p: 1 });
  return ["scrypt", "16384", "8", "1", salt.toString("base64"), key.toString("base64")].join("$");
}

export async function verifyPassword(password: string, stored: string | null | undefined): Promise<boolean> {
  if (!stored) return false;
  const parts = stored.split("$");
  if (parts.length !== 6 || parts[0] !== "scrypt") return false;
  const [, n, r, p, saltB64, keyB64] = parts;
  const expected = Buffer.from(keyB64, "base64");
  try {
    const key = await scrypt(password, Buffer.from(saltB64, "base64"), expected.length, {
      N: Number(n), r: Number(r), p: Number(p),
    });
    return key.length === expected.length && timingSafeEqual(key, expected);
  } catch {
    return false;
  }
}

function keyFrom(b64: string | null): Buffer {
  if (!b64) throw new Error("APP_ENCRYPTION_KEY is not set; secrets cannot be stored");
  const key = Buffer.from(b64, "base64");
  if (key.length !== 32) throw new Error("APP_ENCRYPTION_KEY must be 32 random bytes, base64-encoded");
  return key;
}

export function encryptSecret(plaintext: string, aad: string, keyB64: string | null): string {
  const nonce = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", keyFrom(keyB64), nonce);
  cipher.setAAD(Buffer.from(aad));
  const ct = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  return "v1:" + Buffer.concat([nonce, ct, cipher.getAuthTag()]).toString("base64");
}

export function decryptSecret(token: string, aad: string, keyB64: string | null): string {
  if (!token.startsWith("v1:")) throw new Error("unknown secret format");
  const raw = Buffer.from(token.slice(3), "base64");
  const nonce = raw.subarray(0, 12);
  const tag = raw.subarray(raw.length - 16);
  const ct = raw.subarray(12, raw.length - 16);
  const decipher = createDecipheriv("aes-256-gcm", keyFrom(keyB64), nonce);
  decipher.setAAD(Buffer.from(aad));
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(ct), decipher.final()]).toString("utf8");
}

export function mask(value: string | null | undefined, keep = 3): string {
  if (!value) return "";
  if (value.includes("@")) {
    const [local, domain] = value.split("@", 2);
    return `${local.slice(0, keep)}***@${domain}`;
  }
  return value.length > keep ? `${value.slice(0, keep)}***` : "***";
}
