/** 应用版本号：窗口/网页标题后缀、Tauri 原生标题栏，以及设置页导航栏左下角的版本与构建校验码；执行 bun run test，不访问网络。 */
import { expect, mock, test } from "bun:test";
import { mockWindows } from "@tauri-apps/api/mocks";
import { render, renderHook, screen } from "@testing-library/react";
import { PluginSettings } from "@/features/settings/PluginSettings";
import { useWindowTitle } from "@/lib/window-title";
import { fetchMock, mockDesktop } from "./setup";

// 场景：浏览器里网页标题是「应用名 - v版本号」。
test("网页标题带版本号", () => {
  document.title = "IntelligentMixVideo";
  renderHook(() => useWindowTitle());
  expect(document.title).toBe("IntelligentMixVideo - v1.2.3");
});

// 场景：桌面客户端的原生标题栏不跟随网页标题，需要单独设置，并带同样的版本号。
test("桌面窗口标题栏带版本号", async () => {
  const invoke = mock(async (_command: string, _args: Record<string, unknown>) => null);
  mockDesktop(invoke);
  mockWindows("main");
  renderHook(() => useWindowTitle());
  await Promise.resolve();
  const call = invoke.mock.calls.find(([command]) => command.endsWith("set_title"));
  expect(call?.[1]).toMatchObject({ value: "IntelligentMixVideo - v1.2.3" });
  expect(document.title).toBe("IntelligentMixVideo - v1.2.3");
});

// 场景：原生标题栏设置失败（例如没有权限）不影响页面，网页标题仍已更新。
test("设置原生标题失败不抛出", async () => {
  mockDesktop(async (command) => {
    if (command.endsWith("set_title")) throw new Error("denied");
    return null;
  });
  mockWindows("main");
  document.title = "";
  renderHook(() => useWindowTitle());
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(document.title).toBe("IntelligentMixVideo - v1.2.3");
});

// 场景：拿不到原生窗口信息时也不抛出，页面照常渲染。
test("没有原生窗口信息时保留网页标题", () => {
  mockDesktop(async () => null);
  document.title = "";
  renderHook(() => useWindowTitle());
  expect(document.title).toBe("IntelligentMixVideo - v1.2.3");
});

// 场景：设置页左侧导航栏底部显示版本号，并在其下给出构建校验码（短提交哈希）。
test("设置页导航栏左下角显示版本号和构建校验码", async () => {
  mockDesktop(async () => ({}));
  fetchMock.mockResolvedValueOnce(Response.json([]));
  render(<PluginSettings />);
  const version = await screen.findByLabelText("应用版本");
  expect(version.textContent).toBe("v1.2.3abc1234");
  expect(screen.getByLabelText("构建校验码").textContent).toBe("abc1234");
  expect(screen.getByRole("tablist", { name: "设置模块" }).parentElement).toBe(version.parentElement);
});
