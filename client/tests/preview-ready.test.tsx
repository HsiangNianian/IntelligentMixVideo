/** 隔离预览就绪协议回归：切换版本时 iframe 立即就绪也必须下发参数并解除锁定；关键词字效以示例关键词预览；执行 bun run test。 */
import { expect, spyOn, test } from "bun:test";
import { act, cleanup, render, screen } from "@testing-library/react";
import { PreviewPanel } from "@/features/remotion_templates/PreviewPanel";
import { remotionVersion } from "./remotion-fixtures";
import type { Version } from "@/features/remotion_templates/model";

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

/** 加载预览并回报就绪，返回下发给播放器的参数。 */
async function previewValues(version: Version) {
  render(<PreviewPanel version={version} values={version.candidate.default_config} pending={false} onBusyChange={() => {}} />);
  const iframe = screen.getByTitle<HTMLIFrameElement>("Remotion 字效播放器");
  const post = spyOn(iframe.contentWindow!, "postMessage");
  await act(async () => fromFrame(iframe, { type: "imv-preview-ready", channel: new URL(iframe.src).hash.slice(1) }));
  return post.mock.calls.map(([message]) => message).find((message) => message.type === "imv-preview-update")!.values;
}

// 生成时带关键词字效的字效（有 keywordEffect… 参数）默认关键词为空，预览以默认文字开头两个字为示例关键词并说明；
// 只有模板可调的七个关键词参数时、或已填关键词时按原参数预览。参数本身不变。
test("关键词字效以示例关键词预览，不改参数", async () => {
  const version = remotionVersion("version-1", { keywords: [], keywordEffectScale: 1.3 });
  Object.assign(version.candidate.config_schema.properties, { keywords: { type: "array" }, keywordBold: { type: "boolean" }, keywordEffectScale: { type: "number" } });
  expect(await previewValues(version)).toMatchObject({ text: "今日灵感", keywords: ["今日"] });
  expect(screen.getByText(/预览以「今日」为示例关键词/)).toBeTruthy();
  expect(version.candidate.default_config.keywords).toEqual([]);
  cleanup();
  version.candidate.default_config.keywords = ["灵感"];
  expect((await previewValues(version)).keywords).toEqual(["灵感"]);
  cleanup();
  // 关键词字效开关关闭后不再注入示例关键词。
  Object.assign(version.candidate.default_config, { keywords: [], keywordEffect: false });
  expect((await previewValues(version)).keywords).toEqual([]);
  cleanup();
  const plain = remotionVersion("version-1", { keywords: [] });
  Object.assign(plain.candidate.config_schema.properties, { keywords: { type: "array" }, keywordBold: { type: "boolean" } });
  expect((await previewValues(plain)).keywords).toEqual([]);
  expect(screen.queryByText(/示例关键词/) === null).toBe(true);
  cleanup();
  // 审计复现：以 keyword 开头、却不是 keywordEffect… 的内部参数曾被当作关键词字效，预览注入资产里并不存在的示例高亮。
  const hinted = remotionVersion("version-1", { keywords: [], keywordHint: "#FFFFFF" });
  Object.assign(hinted.candidate.config_schema.properties, { keywords: { type: "array" }, keywordHint: { type: "string" } });
  expect((await previewValues(hinted)).keywords).toEqual([]);
});
