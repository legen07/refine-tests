import { describe, it, expect } from "vitest";
import { add, divide, legacyTotal } from "../src/calc";

describe("calc", () => {
  it("adds numbers alpha", () => {
    const result = add(1, 2);
    expect(result).toBe(3);
  });

  it("adds numbers beta", () => {
    const output = add(1, 2);
    expect(output).toBe(3);
  });

  it("divides two numbers", () => {
    expect(divide(6, 3)).toBe(2);
  });

  it("add is defined", () => {
    expect(add).toBeDefined();
  });

  it("legacy total still used", () => {
    expect(legacyTotal(1, 2)).toBe(3);
  });

  it("sums to snapshot", () => {
    expect(add(2, 3)).toMatchSnapshot();
  });
});
