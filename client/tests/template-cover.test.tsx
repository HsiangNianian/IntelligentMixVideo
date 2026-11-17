/** 验证固定 PNG 地址、版本重新读取及内存重传。运行 bun run test tests/template-cover.test.tsx。 */
import { expect, mock, test } from "bun:test";
import { act, render, waitFor } from "@testing-library/react";
import { Tooltip } from "radix-ui";
import { TemplateCard, TemplateCover } from "@/components/studio/home/TemplateCard";
import { hasCoverBlob, publishCover, readTemplateCover } from "@/features/templates/api";
import { prepareCoverFrame } from "@/features/templates/coverCapture";
import { fetchMock } from "./setup";
import { savedTemplate, templateSummary } from "./fixtures";

// 模拟 SDK 报告定位完成后逐层绘制。前一帧及仅画完一层都不能放行截图。
test("预览已到一秒仍须等同一次绘制中的标题和字幕", async () => {
  const view = render(<div data-cover-ready="true" data-cover-time="0" data-cover-text-layers="2" data-cover-layers="0">
    <div className="template-preview-frame"><div className="template-preview-player"><iframe /></div></div>
  </div>);
  const frame = view.container.querySelector<HTMLElement>(".template-preview-frame")!;
  const doc = frame.querySelector("iframe")!.contentDocument!;
  const canvas = doc.createElement("canvas");
  canvas.id = "subtitle-canvas"; doc.body.append(canvas);
  const clear = mock(() => {}), draw = mock(() => {});
  const ctx = { clearRect: clear, drawImage: draw };
  Object.defineProperty(canvas, "getContext", { value: () => ctx });
  let finished = false;
  const seeking = prepareCoverFrame(frame, seconds => { frame.parentElement!.dataset.coverTime = String(seconds); })
    .then(() => { finished = true; });
  await waitFor(() => expect(frame.parentElement!.dataset.coverTime).toBe("1"));
  ctx.drawImage(); ctx.clearRect(); ctx.drawImage();
  await new Promise(done => setTimeout(done, 100));
  expect(finished).toBe(false);
  ctx.drawImage(); await seeking;
  expect(ctx.drawImage).toBe(draw);
  expect(ctx.clearRect).toBe(clear);
});

// 服务端重建旧包失败时可能继续提供旧播放器；它已经画好的参数不能充当封面帧回执。
test("旧字效宿主无法确认定位时保留旧封面", async () => {
  const view = render(<div data-cover-ready="true" data-cover-time="0" data-cover-text-layers="0" data-cover-layers="1">
    <div className="template-preview-frame"><iframe sandbox="allow-scripts" data-cover-at="1" data-cover-painted="true" data-cover-seek-supported="false" /></div>
  </div>);
  const frame = view.container.querySelector<HTMLElement>(".template-preview-frame")!;
  await expect(prepareCoverFrame(frame, seconds => { frame.parentElement!.dataset.coverTime = String(seconds); }))
    .rejects.toThrow("字效预览包不支持封面定位");
});

// 同步入口集中在搜索框旁；每张卡片只展示该模板的状态和复制、删除等操作。
test("封面失败保留状态提示，恢复后两个陈列区都不出现卡片刷新入口", async () => {
  const template = templateSummary(savedTemplate());
  const props = { editing: false, dirty: false, onOpen: () => {}, onDelete: () => {}, onCopyId: () => {} };
  const card = (cover?: typeof template.cover) => <Tooltip.Provider><TemplateCard template={{ ...template, cover }} {...props} /></Tooltip.Provider>;
  const cover = { url: "https://images.test/cover.png", source: "builtin", revision: "new", publishedRevision: "old" };
  fetchMock.mockResolvedValueOnce(new Response("old")).mockResolvedValueOnce(new Response("new"));
  const view = render(card(cover));
  expect(view.getByText("封面更新失败")).toBeTruthy();
  expect(view.queryByRole("button", { name: "刷新封面" })).toBeNull();
  await act(async () => view.rerender(card({ ...cover, publishedRevision: "new" })));
  expect(view.queryByText("封面更新失败")).toBeNull();
  expect(view.queryByRole("button", { name: "刷新封面" })).toBeNull();
});

// 版本在数据库变化才重新读取同一个地址，来回渲染同一版本不会启动截图。
test("模板封面用固定地址读取新的 PNG", async () => {
  const summary = templateSummary(savedTemplate());
  const cover = { url: "https://images.test/one.png", source: "builtin", revision: "a", publishedRevision: "a" };
  fetchMock.mockResolvedValueOnce(new Response("red")).mockResolvedValueOnce(new Response("blue"));
  const card = render(<TemplateCover template={{ ...summary, cover }} />);
  await waitFor(() => expect(card.container.querySelector("img")).toBeTruthy());
  card.rerender(<TemplateCover template={{ ...summary, name: "新名字", cover }} />);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  card.rerender(<TemplateCover template={{ ...summary, cover: { ...cover, revision: "b", publishedRevision: "b" } }} />);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  const urls = fetchMock.mock.calls.map(([url]) => String(url));
  expect(urls[0]).toBe(urls[1]);
  expect(urls[0]).toEndWith(`/template/${summary.template_id}/cover`);
});

// 两个入口同时挂载，随后只保留一个，最后切换到操练场。
test("最近修改、网格和操练场共用同一张封面，发布新版才再读取", async () => {
  const template = { ...templateSummary(savedTemplate()), template_id: "shared-cover",
    cover: { url: "https://images.test/shared.png", source: "builtin", revision: "a", publishedRevision: "a" } };
  let finish!: (response: Response) => void;
  fetchMock.mockReturnValueOnce(new Promise<Response>(resolve => { finish = resolve; }));
  const gallery = render(<TemplateCover template={template} />);
  const grid = render(<TemplateCover template={template} />);
  gallery.unmount();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => finish(new Response("image a")));
  expect(grid.container.querySelector("img")).toBeTruthy();
  grid.unmount();
  const compact = render(<TemplateCover template={template} compact />);
  await waitFor(() => expect(compact.container.querySelector("img")).toBeTruthy());
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fetchMock.mockResolvedValueOnce(new Response("image b"));
  await act(async () => compact.rerender(<TemplateCover template={{ ...template, cover: { ...template.cover, revision: "b", publishedRevision: "b" } }} compact />));
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

// 读取出错只移除失败的请求，下一次进入可以重新读取同一版本。
test("封面读取失败后同版可以再次请求", async () => {
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 })).mockResolvedValueOnce(new Response("png"));
  await expect(readTemplateCover("read-retry", "v1")).rejects.toThrow("503");
  expect(await (await readTemplateCover("read-retry", "v1")).text()).toBe("png");
});

// 第一次上传失败后，重试发送同一张内存图片，无需重新保存模板或再次截图。
test("上传失败重试只重传该版本的 PNG", async () => {
  const id = "retry-1";
  const blob = new Blob(["png"], { type: "image/png" });
  const captures: number[] = [];
  const capture = async () => { captures.push(1); return blob; };
  fetchMock.mockResolvedValueOnce(new Response("{}", { status: 503 }));
  await expect(publishCover(id, "a", capture)).rejects.toThrow();
  expect(hasCoverBlob(id, "a")).toBe(true);
  fetchMock.mockResolvedValueOnce(Response.json({ url: "https://images.test/retry.png", revision: "a" }));
  expect((await publishCover(id, "a", capture)).url).toEndWith(".png");
  expect(captures).toHaveLength(1);
  expect(hasCoverBlob(id, "a")).toBe(false);
  for (const [url, init] of fetchMock.mock.calls) {
    expect(String(url)).toEndWith("/template/retry-1/cover/a");
    expect(init?.method).toBe("PUT");
    expect(((init?.body as FormData).get("file") as File).type).toBe("image/png");
  }
});

// 已发布的图片要重截；网络重试沿用同一份 PNG 与刷新意图，成功后消费服务端的新版本。
test("手动刷新推进已发布封面的版本", async () => {
  const capture = mock(async () => new Blob(["new png"], { type: "image/png" }));
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 }));
  await expect(publishCover("manual", "a", capture, true)).rejects.toThrow();
  fetchMock.mockResolvedValueOnce(Response.json({ url: "same.png", revision: "b" }));
  expect(await publishCover("manual", "a", capture)).toEqual({ url: "same.png", revision: "b" });
  expect(capture).toHaveBeenCalledTimes(1);
  for (const [, init] of fetchMock.mock.calls) expect((init?.body as FormData).get("refresh")).toBe("true");
});
