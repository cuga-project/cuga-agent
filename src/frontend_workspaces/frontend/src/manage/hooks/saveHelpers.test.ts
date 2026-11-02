import { describe, it, expect } from "vitest";
import { isAbortError, parsePublishedVersion } from "./saveHelpers";

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

describe("parsePublishedVersion", () => {
  it("accepts the string versions the config store returns", () => {
    expect(parsePublishedVersion("3")).toBe(3);
  });

  it("accepts numeric versions", () => {
    expect(parsePublishedVersion(2)).toBe(2);
  });

  it("rejects drafts and non-versions", () => {
    expect(parsePublishedVersion("draft")).toBeNull();
    expect(parsePublishedVersion("")).toBeNull();
    expect(parsePublishedVersion(null)).toBeNull();
    expect(parsePublishedVersion(undefined)).toBeNull();
  });
});
