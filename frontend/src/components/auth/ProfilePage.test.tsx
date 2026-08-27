import { describe, expect, it } from "vitest";
import { maskPhone } from "./userAccess";

describe("profile privacy formatting", () => {
  it("never exposes the full phone in the default view", () => {
    expect(maskPhone("13812345678")).toBe("138****5678");
    expect(maskPhone("12345")).toBe("1***5");
  });

  it("marks missing profile data explicitly", () => {
    expect(maskPhone(null)).toBe("资料待完善");
  });
});
