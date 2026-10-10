/** 主题切换测试：切换 <html> 的 dark 类、保存选择并更新按钮名称；在 client 下执行 bun run test。 */
import { afterEach, expect, test } from "bun:test";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeToggle } from "@/components/ThemeToggle";

afterEach(() => {
  document.documentElement.classList.remove("dark");
  localStorage.clear();
});

// 场景：从浅色切到深色再切回，类名、存储值和无障碍名称同步变化。
test("主题按钮切换明暗并记住选择", () => {
  render(<ThemeToggle collapsed={false} />);
  fireEvent.click(screen.getByRole("button", { name: "切换到深色主题" }));
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  expect(localStorage.getItem("imv.theme")).toBe("dark");
  fireEvent.click(screen.getByRole("button", { name: "切换到浅色主题" }));
  expect(document.documentElement.classList.contains("dark")).toBe(false);
  expect(localStorage.getItem("imv.theme")).toBe("light");
});

// 场景：页面已是深色（首帧脚本恢复）时，按钮初始提供切换到浅色。
test("初始状态读取页面当前主题", () => {
  document.documentElement.classList.add("dark");
  render(<ThemeToggle collapsed />);
  expect(screen.getByRole("button", { name: "切换到浅色主题" })).toBeTruthy();
});
