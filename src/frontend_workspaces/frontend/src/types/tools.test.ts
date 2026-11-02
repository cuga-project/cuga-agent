import { describe, it, expect } from "vitest";
import { parseGatewayInstanceUrl } from "./tools";

describe("parseGatewayInstanceUrl", () => {
  it("returns the instance id of an instance URL", () => {
    expect(parseGatewayInstanceUrl(" https://gw.example.com/?instance=7f3c9a10-5d2e ")).toBe("7f3c9a10-5d2e");
  });

  it.each([
    "",
    "gw.example.com/?instance=abc",
    "ftp://gw.example.com/?instance=abc",
    "https://gw.example.com/",
    "https://gw.example.com/?instance=",
    "https://gw.example.com/?instance=../admin",
  ])("rejects %s", (url) => {
    expect(parseGatewayInstanceUrl(url)).toBeNull();
  });
});
