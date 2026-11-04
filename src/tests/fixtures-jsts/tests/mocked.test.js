import { describe, it, expect, vi } from "vitest";
import { divide } from "../src/calc";

describe("mocked", () => {
  it("mock only", () => {
    const add = vi.fn(() => 4);
    expect(add(1, 2)).toBe(4);
    expect(add).toHaveBeenCalled();
  });

  it("divide by zero throws", () => {
    expect(() => divide(1, 0)).toThrow("division by zero");
  });
});
