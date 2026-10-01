import { describe, expect, it } from "vitest";
import { verifySignature } from "../src/signature.js";
import { sign } from "./helpers.js";

describe("verifySignature", () => {
  const body = '{"a":1}';

  it("accepts the correct signature", () => {
    expect(verifySignature("s3cret", body, sign(body, "s3cret"))).toBe(true);
  });

  it("rejects a signature made with another secret", () => {
    expect(verifySignature("s3cret", body, sign(body, "other"))).toBe(false);
  });

  it("rejects a tampered body", () => {
    expect(verifySignature("s3cret", body + " ", sign(body, "s3cret"))).toBe(false);
  });

  it("rejects missing, malformed and wrong-length headers without throwing", () => {
    expect(verifySignature("s3cret", body, undefined)).toBe(false);
    expect(verifySignature("s3cret", body, "")).toBe(false);
    expect(verifySignature("s3cret", body, "sha1=abc")).toBe(false);
    expect(verifySignature("s3cret", body, "sha256=zz")).toBe(false);
    expect(verifySignature("s3cret", body, "sha256=" + "0".repeat(64))).toBe(false);
  });

  it("rejects an empty secret even if the signature matches", () => {
    expect(verifySignature("", body, sign(body, ""))).toBe(false);
  });
});
