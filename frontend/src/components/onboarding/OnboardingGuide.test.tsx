import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";
import { OnboardingGuide } from "./OnboardingGuide";
import { useAuthStore } from "../../stores/authStore";

describe("OnboardingGuide", () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({ user: { id: "admin", username: "admin", role: "admin" } });
  });

  it("guides first-time admins and remembers completion", async () => {
    render(<MemoryRouter><OnboardingGuide /></MemoryRouter>);
    expect(screen.getByRole("dialog", { name: "首次配置向导" })).toBeInTheDocument();
    expect(screen.getByText("连接模型")).toBeInTheDocument();

    await userEvent.click(screen.getByText("下一步"));
    expect(screen.getByText("连接数据库")).toBeInTheDocument();
    await userEvent.click(screen.getByText("稍后再说"));

    expect(localStorage.getItem("sql_rpa_onboarding_completed_v1")).toBe("true");
    expect(screen.queryByRole("dialog", { name: "首次配置向导" })).not.toBeInTheDocument();
  });
});
