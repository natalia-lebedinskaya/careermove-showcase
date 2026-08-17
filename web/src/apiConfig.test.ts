import { describe, expect, it } from "vitest";
import { getApiTargets } from "./apiConfig";

describe("getApiTargets", () => {
  it("uses the configured API without a trailing slash", () => {
    expect(getApiTargets("https://api.example.com/", true)).toEqual(["https://api.example.com"]);
  });

  it("uses the local API during development", () => {
    expect(getApiTargets("", false)).toEqual(["http://127.0.0.1:8080"]);
  });

  it("keeps an unconfigured production build on the same origin", () => {
    expect(getApiTargets("", true)).toEqual([""]);
  });
});
