/** 桌面启动门禁：等待就绪、失败诊断、卸载保护及两个功能共享动态端口，不启动真实服务。 */
import { afterEach, expect, test } from "bun:test";
import { act, render, screen, waitFor } from "@testing-library/react";
import App from "@/App";
import { apiBase, setApiBase } from "@/lib/api-base";
import { apiUrl, create } from "@/features/remotion_templates/api";
import { listTemplates } from "@/features/templates/api";
import { fetchMock, mockDesktop } from "./setup";
import { remotionServer } from "./remotion-server";
import { remotionJob } from "./remotion-fixtures";

const originalBase = apiBase();
afterEach(() => setApiBase(originalBase));

// 合并上游画布配置后，生成请求仍发往内置后端端口，并完整保留用户配置。
test("内置后端接收上游新增的生成配置", async () => {
  setApiBase("http://127.0.0.1:43212");
  const composition = { width: 1280, height: 720, fps: 24, duration_in_frames: 72 };
  const response = { work: { id: "work-1" }, job: remotionJob("running") };
  fetchMock.mockResolvedValueOnce(Response.json(response));
  expect(await create("横屏标题", undefined, composition)).toEqual(response);
  const [url, options] = fetchMock.mock.calls.at(-1)!;
  expect(url).toBe("http://127.0.0.1:43212/api/templates/works");
  expect(options?.method).toBe("POST");
  expect(JSON.parse(String(options?.body))).toEqual({ description: "横屏标题", composition });
});

// 内置服务未就绪时不发送业务请求，就绪后模板、预览及 SSE 均使用回执端口。
test("桌面等待内置服务并共享实际 API 地址", async () => {
  let ready!: (url: string) => void;
  const reset = mockDesktop(async (command) => {
    if (command === "local_settings") return {};
    expect(command).toBe("start_backend");
    return new Promise<string>((resolve) => { ready = resolve; });
  });
  const view = render(<App />);
  try {
    expect(screen.getByRole("status").textContent).toContain("正在启动内置服务");
    expect(fetchMock).not.toHaveBeenCalled();
    remotionServer();
    await waitFor(() => expect(ready).toBeFunction());
    await act(async () => ready("http://127.0.0.1:43210"));
    expect(screen.getByRole("heading", { name: "特效模板" })).toBeTruthy();
    expect(apiUrl("/works")).toBe("http://127.0.0.1:43210/api/templates/works");
    fetchMock.mockResolvedValueOnce(Response.json([]));
    await listTemplates();
    expect(fetchMock.mock.calls.at(-1)?.[0]).toBe("http://127.0.0.1:43210/template");
  } finally {
    view.unmount();
    reset();
  }
});

// 启动失败停留诊断页，不让业务界面连续报网络连接错误。
test("启动失败保留具体诊断且不请求 API", async () => {
  const reset = mockDesktop(async () => { throw "数据库启动失败，请查看 server.log"; });
  const view = render(<App />);
  try {
    expect((await screen.findByRole("alert")).textContent).toContain("数据库启动失败");
    expect(fetchMock).not.toHaveBeenCalled();
  } finally {
    view.unmount();
    reset();
  }
});

// 卸载后的迟到回执不能改变全局 API 地址。
test("卸载后忽略迟到的内置服务回执", async () => {
  let ready!: (url: string) => void;
  const reset = mockDesktop(async () => new Promise<string>((resolve) => { ready = resolve; }));
  const view = render(<App />);
  view.unmount();
  try {
    await act(async () => ready("http://127.0.0.1:43211"));
    expect(apiBase()).toBe(originalBase);
    expect(fetchMock).not.toHaveBeenCalled();
  } finally {
    reset();
  }
});
