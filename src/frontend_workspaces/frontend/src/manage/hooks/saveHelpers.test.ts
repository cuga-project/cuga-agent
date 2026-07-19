import { describe, it, expect } from "vitest";
import { isAbortError } from "./saveHelpers";

describe("isAbortError", () => {
  it("detects DOMException AbortError", () => {
    expect(isAbortError(new DOMException("aborted", "AbortError"))).toBe(true);
  });

  it("detects Error with name AbortError", () => {
    const e = new Error("aborted");
    e.name = "AbortError";
    expect(isAbortError(e)).toBe(true);
  });

  it("rejects other errors", () => {
    expect(isAbortError(new Error("network"))).toBe(false);
    expect(isAbortError(null)).toBe(false);
  });
});
