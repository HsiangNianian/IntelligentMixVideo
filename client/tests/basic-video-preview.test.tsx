/**
 * IMS 不可用时的基础视频预览：降级早于 SDK 加载时画布、AI 资产与时间轴行照常显示；执行 bun run test。
 */
import { expect, test } from "bun:test";
import { render, screen } from "@testing-library/react";
import { TemplatePreview } from "@/features/templates/TemplatePreview";
import { newDraft } from "@/features/templates/model";
import { previewContext, resolveTemplate } from "@/features/templates/tracks";
import { sampleMedia } from "./fixtures";

// 显示在 1～4 秒的 AI 资产，素材完整时长 5 秒。
const sprite = { id: "sprite-a", spriteId: "s1", aspect: 9 / 16, name: "霓虹标题", start: 1, end: 4, length: 5 };

/** 挂载模板预览：测试环境拒绝所有网络请求，预览字体下载失败，SDK 加载前就降级为基础视频。 */
function mountEarlyFallback(props: Partial<Parameters<typeof TemplatePreview>[0]> = {}) {
  const draft = newDraft();
  const view = render(<TemplatePreview draft={draft} media={sampleMedia} resolved={resolveTemplate(draft, previewContext(sampleMedia))}
    videoInputKey="basic" onMediaChange={() => {}} onCatalog={() => {}} {...props} />);
  return { draft, view };
}

// 回归：降级早于 SDK 加载（这里是预览字体下载失败）时画布尺寸从未设置，AI 资产叠加层（Remotion 字效等）不显示，时间轴也没有对象与视频行。
// 降级只能停用 IMS 侧的文字与特效：画布、AI 资产和时间轴行都要照常显示，时间轴用随包效果目录按草稿生成。
test("降级早于 SDK 加载时画布、AI 资产与时间轴行照常显示", async () => {
  mountEarlyFallback({ sprites: [sprite] });
  await screen.findByRole("button", { name: "重试 IMS 预览" });
  expect(screen.getByText("1080 × 1920")).toBeTruthy();
  expect(screen.getByTitle("AI 资产预览：霓虹标题")).toBeTruthy();
  const rows = [...screen.getByLabelText("时间轴轨道标签").querySelectorAll("[data-object]")].map((label) => label.getAttribute("data-object"));
  expect(rows).toEqual(expect.arrayContaining(["title", "subtitle", "transition", "video"]));
});
