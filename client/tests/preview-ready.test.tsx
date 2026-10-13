/** 隔离预览就绪协议回归：切换版本时 iframe 立即就绪也必须下发参数并解除锁定；执行 bun run test。 */
import { expect, spyOn, test } from "bun:test";
import { act, render, screen } from "@testing-library/react";
import { PreviewPanel } from "@/features/remotion_templates/PreviewPanel";
import { remotionVersion } from "./remotion-fixtures";

/** 以播放器 iframe 的身份向父页面发送一条协议消息。 */
function fromFrame(iframe: HTMLIFrameElement, data: Record<string, unknown>) {
  window.dispatchEvent(new MessageEvent("message", { source: iframe.contentWindow, data }));
}

// 审计复现：返回最新版本时，若 iframe 在 setReady(false) 渲染前就回报就绪，false→true 被合并成「未变化」，
// 参数不再下发，预览停在「正在渲染预览…」并锁住参数 20 秒，随后报加载超时。
test("切换版本时立即就绪的预览仍下发参数并解锁", async () => {
  const busy: boolean[] = [];
  /** 记录工作区收到的锁定状态，最后一次必须是解锁。 */
  const onBusyChange = (value: boolean) => busy.push(value);
  const first = remotionVersion("version-1");
  const second = remotionVersion("version-2");
  const values = first.candidate.default_config;
  const { rerender } = render(
    <PreviewPanel version={first} values={values} pending={false} onBusyChange={onBusyChange} />,
  );
  const iframe = screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器");
  const post = spyOn(iframe.contentWindow!, "postMessage");
  await act(async () => fromFrame(iframe, { type: "imv-preview-ready", channel: new URL(iframe.src).hash.slice(1) }));
  await act(async () => fromFrame(iframe, { type: "imv-preview-rendered", channel: post.mock.calls.at(-1)![0].channel, requestId: post.mock.calls.at(-1)![0].requestId }));
  expect(screen.queryByText("正在渲染预览…")).toBeNull();
  // 之后的加载都像缓存命中一样瞬时完成：赋值 src 的同时 iframe 就回报就绪。
  let src = iframe.src;
  Object.defineProperty(iframe, "src", {
    configurable: true,
    get: () => src,
    /** 保存地址并同步回报新通道已就绪。 */
    set(value: string) {
      src = value;
      fromFrame(iframe, { type: "imv-preview-ready", channel: new URL(value, location.href).hash.slice(1) });
    },
  });
  post.mockClear();
  await act(async () => {
    rerender(<PreviewPanel version={second} values={values} pending={false} onBusyChange={onBusyChange} />);
  });
  const channel = new URL(src, location.href).hash.slice(1);
  const update = post.mock.calls.map(([message]) => message).find((message) => message.type === "imv-preview-update");
  expect(update?.channel).toBe(channel);
  await act(async () => fromFrame(iframe, { type: "imv-preview-rendered", channel, requestId: update.requestId }));
  expect(screen.queryByText("正在渲染预览…")).toBeNull();
  expect(busy.at(-1)).toBe(false);
});
