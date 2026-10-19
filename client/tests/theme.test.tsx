/** 主题回归：切换、持久化、首帧恢复与存储不可用；在 client 下执行 bun run test。 */
import { afterEach, expect, spyOn, test } from "bun:test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeToggle } from "@/components/ThemeToggle";

const bootstrap = readFileSync(new URL("../index.html", import.meta.url), "utf8").match(/<script>([\s\S]*?)<\/script>/)![1];

/** 执行真实 HTML 中的首帧脚本，只替换系统偏好，不挂载 React。 */
function restore(systemDark: boolean) {
  runInNewContext(bootstrap, { document, localStorage, matchMedia: () => ({ matches: systemDark }) });
}

afterEach(() => {
  document.documentElement.classList.remove("dark");
  localStorage.clear();
});

// 场景：双向切换同步页面和按钮，重新打开时仍使用保存的选择。
test("主题按钮双向切换并在重启后恢复", () => {
  render(<ThemeToggle />);
  fireEvent.click(screen.getByRole("button", { name: "主题", pressed: false }));
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  expect(localStorage.getItem("imv.theme")).toBe("dark");
  document.documentElement.classList.remove("dark");
  restore(false);
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "主题", pressed: true }));
  expect(document.documentElement.classList.contains("dark")).toBe(false);
  expect(localStorage.getItem("imv.theme")).toBe("light");
  restore(true);
  expect(document.documentElement.classList.contains("dark")).toBe(false);
});

// 场景：首帧已恢复暗色时，侧栏正确显示当前模式及切换方向。
test("主题按钮识别首帧已恢复的暗色", () => {
  document.documentElement.classList.add("dark");
  render(<ThemeToggle />);
  expect(screen.getByRole("button", { name: "主题", pressed: true }).textContent).toBe("主题");
});

// 场景：首次访问或无效历史值采用系统偏好，不把未知值固定解释为亮色。
test("没有有效选择时首帧跟随系统", () => {
  restore(true);
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  localStorage.setItem("imv.theme", "invalid");
  restore(false);
  expect(document.documentElement.classList.contains("dark")).toBe(false);
});

// 场景：隐私模式拒绝存储时，系统暗色仍可恢复且用户能够切回亮色。
test("存储被禁用时仍可初始化和切换主题", () => {
  spyOn(localStorage, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  spyOn(localStorage, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  restore(true);
  render(<ThemeToggle />);
  fireEvent.click(screen.getByRole("button", { name: "主题", pressed: true }));
  expect(document.documentElement.classList.contains("dark")).toBe(false);
  expect(screen.getByRole("button", { name: "主题", pressed: false })).toBeTruthy();
});
