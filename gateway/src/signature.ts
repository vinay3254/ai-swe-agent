import { createHmac, timingSafeEqual } from "node:crypto";

/** Check GitHub's `X-Hub-Signature-256` header against the raw request body. */
export function verifySignature(secret: string, rawBody: string, header: string | undefined): boolean {
  if (!secret || !header?.startsWith("sha256=")) return false;
  const hex = header.slice("sha256=".length);
  if (!/^[0-9a-f]{64}$/i.test(hex)) return false;
  const expected = createHmac("sha256", secret).update(rawBody).digest();
  return timingSafeEqual(expected, Buffer.from(hex, "hex"));
}
