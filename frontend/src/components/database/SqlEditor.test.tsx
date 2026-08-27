import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SqlEditor } from "./SqlEditor";

function renderEditor(canWrite = true) {
  const onExecute = vi.fn();
  const onPreview = vi.fn();
  render(
    <SqlEditor
      onExecute={onExecute}
      onPreview={onPreview}
      loading={false}
      canWrite={canWrite}
    />,
  );
  return { onExecute, onPreview };
}

describe("SqlEditor", () => {
  it("executes a read query through the execute button", () => {
    const { onExecute } = renderEditor();
    const textarea = screen.getByPlaceholderText(/输入 SQL 语句/);

    fireEvent.change(textarea, { target: { value: "SELECT * FROM users" } });
    fireEvent.click(screen.getByText("执行查询"));

    expect(onExecute).toHaveBeenCalledWith("SELECT * FROM users");
  });

  it("blocks write submission for the viewer role", () => {
    renderEditor(false);
    const textarea = screen.getByPlaceholderText(/输入 SQL 语句/);

    fireEvent.change(textarea, {
      target: { value: "DELETE FROM users WHERE id = 1" },
    });

    expect(screen.getByText("预览影响").closest("button")).toBeDisabled();
  });
});
