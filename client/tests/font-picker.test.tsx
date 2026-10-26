/** 字体选择器与字体库页面：展示实时字体库、搜索、选择，以及上传自己的字体（显示名取字体自己的名称、浏览器读不了、服务端拒绝与成功后立即选中）；执行 bun run test，不访问网络。 */
import { expect, spyOn, test } from "bun:test";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ParameterControl } from "@/components/ParameterControl";
import type { FontFaceInfo, FontInfo } from "@/features/fonts/api";
import { FontLibrary } from "@/features/fonts/FontLibrary";
import HomePage from "@/pages/HomePage";
import { FontPicker } from "@/features/fonts/FontPicker";
import { repairFont } from "@/features/fonts/repair";
import type { FaceLoader } from "@/features/fonts/FontSpecimen";
import { Toaster } from "@/components/ui/sonner";
import { english, fontFile } from "./font-files";
import { fetchMock } from "./setup";

/** 字体卡片里的一个字形。 */
const face = (id: string, slot: FontFaceInfo["slot"], extra: Partial<FontFaceInfo> = {}): FontFaceInfo => ({
  id, slot, weights: [400], size: 1000, sha256: "", file_url: `/api/templates/fonts/${id}`, preview_url: `/api/templates/fonts/${id}/preview`, ...extra,
});
/** 一张字体卡片；默认只有一个常规字形。 */
const font = (id: string, family: string, label: string, extra: Partial<FontInfo> = {}): FontInfo => ({
  id, family, label, style: "sample style", weights: [400], source: "builtin", license: "", size: 1000,
  file_url: `/api/templates/fonts/${id}`, preview_url: `/api/templates/fonts/${id}/preview`, faces: [face(id, "regular")], ...extra,
});
const LIBRARY = [
  font("noto-sans-cjk", "Noto Sans CJK SC", "思源黑体（默认）", {
    source: "system", file_url: null, size: 0, style: "default clean Hei",
    faces: [face("noto-sans-cjk", "regular", { weights: [400, 700], size: 0, file_url: null })],
  }),
  font("ma-shan-zheng", "Ma Shan Zheng", "马善政毛笔楷书", { style: "brush calligraphy", license: "OFL-1.1" }),
];

const UPLOADED = font("u-1", "Zed Display", "我的字体", { source: "upload" });
/** 文件名是一串哈希、英文家族名 Zed Display、简体中文名“我的字体”的字体文件。 */
const uploadFile = () => fontFile("0b7027d34f9546b7993e082b48c22401.ttf", [english("Zed Display"), [3, 1, 0x0804, 1, "我的字体"]]);

/** 字体库接口：GET 返回当前列表，POST 按 `onUpload` 的结果响应；记录上传请求的表单。 */
function fontServer(onUpload?: (form: FormData) => Response, initial: FontInfo[] = LIBRARY) {
  const library = [...initial];
  const uploads: FormData[] = [];
  fetchMock.mockImplementation((async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    if (url.pathname !== "/api/templates/fonts") return new Response("未配置", { status: 500 });
    if (init?.method === "POST") {
      const form = init.body as FormData;
      uploads.push(form);
      const response = onUpload!(form);
      if (response.ok) library.push(UPLOADED);
      return response;
    }
    return Response.json(library);
  }) as typeof fetch);
  return { uploads };
}

/** 打开下拉框并等字体库到位。 */
async function open(onChange: (family: string) => void, previewer?: (file: File, text: string) => Promise<Blob | null>) {
  render(<FontPicker label="字体" value="Noto Sans CJK SC" onChange={onChange} previewer={previewer} />);
  await screen.findByRole("img", { name: "思源黑体（默认）" });
  fireEvent.click(screen.getByRole("combobox", { name: "字体" }));
  await screen.findByRole("option", { name: /马善政毛笔楷书/ });
}

// 场景：字体库来自服务端；每个选项以预览条展示字体本身的样子并带家族名，当前值打勾，选择后回传家族名并关闭。
test("下拉框展示实时字体库并回传所选家族名", async () => {
  fontServer();
  const changes: string[] = [];
  await open((family) => changes.push(family));
  expect(screen.getByRole("option", { name: /思源黑体/ }).querySelector("svg")?.classList.contains("opacity-100")).toBe(true);
  expect(screen.getByRole("img", { name: "马善政毛笔楷书" }).getAttribute("style")).toContain("http://api.test:8000/api/templates/fonts/ma-shan-zheng/preview");
  fireEvent.click(screen.getByRole("option", { name: /马善政毛笔楷书/ }));
  expect(changes).toEqual(["Ma Shan Zheng"]);
  await waitFor(() => expect(screen.queryByRole("option", { name: /马善政毛笔楷书/ })).toBeNull());
});

// 场景：可以按名称或风格搜索字体（例如输入“brush”只留下毛笔字体）。
test("搜索按名称和风格过滤字体", async () => {
  fontServer();
  await open(() => {});
  fireEvent.change(await screen.findByPlaceholderText("搜索字体"), { target: { value: "brush" } });
  await waitFor(() => expect(screen.queryByRole("option", { name: /思源黑体/ })).toBeNull());
  expect(screen.getByRole("option", { name: /马善政毛笔楷书/ })).toBeTruthy();
});

// 场景：上传字体——浏览器用字体自己的名称（中文名优先，不是哈希文件名）画出预览条，再把字体、显示名与预览条一起上传；
// 成功后字库刷新，新字体被选中。
test("上传字体后立即选中", async () => {
  const server = fontServer(() => Response.json(UPLOADED, { status: 201 }));
  const changes: string[] = [];
  const strip = new Blob(["png"], { type: "image/png" });
  const drawn: string[] = [];
  await open((family) => changes.push(family), async (_file, text) => { drawn.push(text); return strip; });
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [uploadFile()] } });
  await waitFor(() => expect(changes).toEqual(["Zed Display"]));
  expect(drawn).toEqual(["我的字体"]);
  const form = server.uploads[0]!;
  expect((form.get("file") as File).name).toBe("0b7027d34f9546b7993e082b48c22401.ttf");
  expect(form.get("label")).toBe("我的字体");
  const preview = form.get("preview") as Blob;
  expect([preview.type, preview.size]).toEqual(["image/png", 3]);
});

// 场景：浏览器读不了这个字体文件（预览条画不出来）——不发上传请求，说明浏览器无法解码。
test("无法解码的字体文件不会被上传", async () => {
  const server = fontServer(() => Response.json(UPLOADED, { status: 201 }));
  const changes: string[] = [];
  await open((family) => changes.push(family), async () => { throw new Error("bad font"); });
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [fontFile("broken.ttf", [english("Broken")])] } });
  expect((await screen.findByRole("alert")).textContent).toContain("浏览器无法解码这个字体文件");
  expect(server.uploads).toEqual([]);
  expect(changes).toEqual([]);
});

// 场景：读不出名称、浏览器也解码不了的文件——同样不发上传请求（解码检查不因读不出名称而跳过）。
test("读不出名称且无法解码的文件不会被上传", async () => {
  const server = fontServer(() => Response.json(UPLOADED, { status: 201 }));
  await open(() => undefined, async () => { throw new Error("bad font"); });
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [new File(["x"], "notes.ttf")] } });
  expect((await screen.findByRole("alert")).textContent).toContain("浏览器无法解码这个字体文件");
  expect(server.uploads).toEqual([]);
});

// 场景：做字工具导出的字体带零长度的 cvt 表（系统里能装，浏览器 OTS 拒绝）——上传前删掉这张表，
// 预览与上传都用修复后的字节，文件名与显示名不变。
test("上传前自动修复零长度的表", async () => {
  const server = fontServer(() => Response.json(UPLOADED, { status: 201 }));
  const changes: string[] = [];
  const drawn: File[] = [];
  await open((family) => changes.push(family), async (file) => { drawn.push(file); return null; });
  const file = fontFile("0b7027d34f9546b7993e082b48c22401.ttf", [english("Zed Display"), [3, 1, 0x0804, 1, "我的字体"]], [["cvt ", new Uint8Array(0)]]);
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [file] } });
  await waitFor(() => expect(changes).toEqual(["Zed Display"]));
  const uploaded = server.uploads[0]!.get("file") as File;
  expect(drawn[0]).toBe(uploaded);
  const view = new DataView(await uploaded.arrayBuffer());
  const tags = Array.from({ length: view.getUint16(4) }, (_, index) => {
    const at = 12 + 16 * index;
    return String.fromCharCode(view.getUint8(at), view.getUint8(at + 1), view.getUint8(at + 2), view.getUint8(at + 3)).trimEnd();
  });
  expect(tags).toEqual(["name"]);
  expect([uploaded.name, server.uploads[0]!.get("label")]).toEqual(["0b7027d34f9546b7993e082b48c22401.ttf", "我的字体"]);
});

// 场景：修掉零长度表后浏览器仍解不出来——提示里说明已经自动修过什么，仍不发上传请求。
test("修复后仍无法解码时说明已修复", async () => {
  const server = fontServer(() => Response.json(UPLOADED, { status: 201 }));
  await open(() => undefined, async () => { throw new Error("bad font"); });
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [fontFile("fixed.ttf", [english("Fixed")], [["cvt ", new Uint8Array(0)]])] } });
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("仍无法解码");
  expect(alert.textContent).toContain("已自动修复：移除了空的「cvt」表");
  expect(server.uploads).toEqual([]);
});

// 场景：目录里必需表被写成零长度（内容已经丢了）——不发上传请求，说明是哪张表损坏。
test("结构损坏的字体不会被上传", async () => {
  const server = fontServer(() => Response.json(UPLOADED, { status: 201 }));
  await open(() => undefined, async () => null);
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [fontFile("broken.ttf", [english("Broken")], [["loca", new Uint8Array(0)]])] } });
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("字体文件结构损坏");
  expect(alert.textContent).toContain("「loca」表是空的");
  expect(server.uploads).toEqual([]);
});

// 场景：选择器上传前修过文件——用轻提示说明修了什么，同时照常选中。
test("选择器上传前修复字体时提示并选中", async () => {
  fontServer(() => Response.json(UPLOADED, { status: 201 }));
  const changes: string[] = [];
  render(<><Toaster /><FontPicker label="字体" value="Noto Sans CJK SC" onChange={(family) => changes.push(family)} previewer={async () => null} /></>);
  await screen.findByRole("img", { name: "思源黑体（默认）" });
  fireEvent.click(screen.getByRole("combobox", { name: "字体" }));
  await screen.findByRole("option", { name: /马善政毛笔楷书/ });
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [fontFile("fixed.ttf", [english("Zed Display")], [["cvt ", new Uint8Array(0)]])] } });
  expect(await screen.findByText(/已自动修复字体文件后上传：移除了空的「cvt」表/)).toBeTruthy();
  expect(changes).toEqual(["Zed Display"]);
});

// 场景：读不出字体名称但浏览器能解码——只做解码检查，不保留预览条、不用文件名当显示名，由服务端判断并展示它给出的原因。
test("读不出名称的文件交给服务端判断", async () => {
  const server = fontServer(() => Response.json({ detail: "只支持 TrueType / OpenType 字体文件（.ttf、.otf）" }, { status: 422 }));
  const drawn: string[] = [];
  await open(() => undefined, async (_file, text) => { drawn.push(text); return new Blob(["png"], { type: "image/png" }); });
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [new File(["x"], "notes.ttf")] } });
  expect((await screen.findByRole("alert")).textContent).toContain("只支持 TrueType / OpenType");
  expect(drawn).toEqual([""]);
  expect([server.uploads[0]!.get("label"), server.uploads[0]!.get("preview")]).toEqual(["", null]);
});

// 场景：服务端拒绝（同一个文件已在库里）——把服务端给出的原因原样展示在浮层里，不改变当前选择。
test("服务端拒绝上传时展示原因", async () => {
  fontServer(() => Response.json({ detail: "这个字体文件已经在字体库里了：我的字体（Zed Display）" }, { status: 422 }));
  const changes: string[] = [];
  await open((family) => changes.push(family), async () => null);
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [new File(["x"], "zed.ttf")] } });
  expect((await screen.findByRole("alert")).textContent).toContain("这个字体文件已经在字体库里了：我的字体（Zed Display）");
  expect(changes).toEqual([]);
});

/** 文件内容的 SHA-256（十六进制），与字形的 sha256 字段对应。 */
async function digestOf(file: File) {
  return Buffer.from(await crypto.subtle.digest("SHA-256", await file.arrayBuffer())).toString("hex");
}

// 场景（回归）：上传超时或断线，而服务端其实已经登记了字体——原先只显示失败、列表不刷新，重试又被告知「已在字体库里」却选不到；
// 现在失败后重新读取字体库，按文件摘要找到它就当作已在库里，直接选中，不显示错误。
test("上传请求失败但服务端已登记时选中该字体", async () => {
  const file = uploadFile();
  const stored = font("u-1", "Zed Display", "我的字体", { source: "upload", faces: [face("u-1", "regular", { sha256: await digestOf(file) })] });
  const library = [...LIBRARY];
  let posts = 0;
  fetchMock.mockImplementation((async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method !== "POST") return Response.json(library);
    posts++;
    library.push(stored);
    throw new TypeError("Load failed");
  }) as typeof fetch);
  const changes: string[] = [];
  await open((family) => changes.push(family), async () => new Blob(["png"], { type: "image/png" }));
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [file] } });
  await waitFor(() => expect(changes).toEqual(["Zed Display"]));
  expect(posts).toBe(1);
  expect(screen.queryByRole("alert")).toBeNull();
});

// 场景：字体库里已经有同一个文件（别人传过，或之前的上传其实成功了）——不再上传、也不必画预览条，直接选中它。
test("字体库里已有同一个文件时直接选中，不再上传", async () => {
  const file = uploadFile();
  const existing = font("u-1", "Zed Display", "我的字体", { source: "upload", faces: [face("u-1", "regular", { sha256: await digestOf(file) })] });
  const server = fontServer(() => Response.json(UPLOADED, { status: 201 }), [...LIBRARY, existing]);
  const changes: string[] = [];
  const drawn: string[] = [];
  await open((family) => changes.push(family), async (_file, text) => { drawn.push(text); return null; });
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [file] } });
  await waitFor(() => expect(changes).toEqual(["Zed Display"]));
  expect(server.uploads).toEqual([]);
  expect(drawn).toEqual([]);
});

// 场景：字体库里已有的是修复后的字节——按修复后的摘要去重，不再上传，也不必画预览条。
test("字体库里已有修复后的文件时直接选中，不再上传", async () => {
  const file = fontFile("fixed.ttf", [english("Zed Display"), [3, 1, 0x0804, 1, "我的字体"]], [["cvt ", new Uint8Array(0)]]);
  const repaired = repairFont(await file.arrayBuffer());
  expect(repaired.kind).toBe("repaired");
  if (repaired.kind !== "repaired") return;
  const digest = Buffer.from(await crypto.subtle.digest("SHA-256", repaired.bytes)).toString("hex");
  const existing = font("u-1", "Zed Display", "我的字体", { source: "upload", faces: [face("u-1", "regular", { sha256: digest })] });
  const server = fontServer(() => Response.json(UPLOADED, { status: 201 }), [...LIBRARY, existing]);
  const changes: string[] = [];
  const drawn: string[] = [];
  await open((family) => changes.push(family), async (_file, text) => { drawn.push(text); return null; });
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [file] } });
  await waitFor(() => expect(changes).toEqual(["Zed Display"]));
  expect(server.uploads).toEqual([]);
  expect(drawn).toEqual([]);
});

// 场景（回归）：浏览器有 crypto.subtle 但摘要计算失败（权限策略、受限 WebView）——摘要只是优化，不能挡住上传，
// 继续把文件交给按内容幂等的服务端。
test("无法计算文件摘要时仍上传", async () => {
  const digest = spyOn(crypto.subtle, "digest").mockRejectedValue(new Error("digest unavailable"));
  try {
    const server = fontServer(() => Response.json(UPLOADED, { status: 201 }));
    const changes: string[] = [];
    await open((family) => changes.push(family), async () => null);
    fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [uploadFile()] } });
    await waitFor(() => expect(changes).toEqual(["Zed Display"]));
    expect(server.uploads).toHaveLength(1);
  } finally {
    digest.mockRestore();
  }
});

// 场景（回归）：服务端明确拒绝（413）：文件没有登记，失败后不再多读一遍字体库，直接显示原因。
test("上传被 413 拒绝时不再重读字体库", async () => {
  let gets = 0;
  fetchMock.mockImplementation((async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") return Response.json({ detail: "字体文件超过 50 MiB 上限" }, { status: 413 });
    gets++;
    return Response.json(LIBRARY);
  }) as typeof fetch);
  await open(() => undefined, async () => null);
  expect(gets).toBe(1);
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [uploadFile()] } });
  expect((await screen.findByRole("alert")).textContent).toContain("50 MiB");
  expect(gets).toBe(1);
});

// 场景：参数面板里名为 fontFamily 的参数、以及双栏字效每栏的字体（line1FontFamily / line2FontFamily）使用字体选择器，
// 而不是普通文本框或冻结的枚举下拉。
test.each([["fontFamily", "字体"], ["line2FontFamily", "下栏字体"]])("%s 参数控件是字体选择器", async (key, label) => {
  fontServer();
  const changes: unknown[] = [];
  render(<ParameterControl field={{ key, label, kind: "string", maxLength: 100 }} value="Noto Sans CJK SC" disabled={false} onChange={(value) => changes.push(value)} />);
  await screen.findByRole("img", { name: "思源黑体（默认）" });
  expect(screen.queryByRole("textbox")).toBeNull();
  fireEvent.click(screen.getByRole("combobox", { name: label }));
  fireEvent.click(await screen.findByRole("option", { name: /马善政毛笔楷书/ }));
  expect(changes).toEqual(["Ma Shan Zheng"]);
});

// 场景：字体库页面列出全部字体（来源、字重、大小），可按风格搜索；读取失败时给出重试。
test("字体库页面列出全部字体并支持搜索", async () => {
  fontServer();
  render(<FontLibrary />);
  expect(await screen.findByText("共 2 个字体")).toBeTruthy();
  expect(screen.getAllByRole("img").map((item) => item.getAttribute("aria-label"))).toEqual(["思源黑体（默认）", "马善政毛笔楷书"]);
  expect(screen.getByText("系统默认")).toBeTruthy();
  expect(screen.getByText("随系统安装")).toBeTruthy();
  expect(screen.getByText("预装")).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox", { name: "搜索字体" }), { target: { value: "brush" } });
  await waitFor(() => expect(screen.getByText("共 2 个字体，匹配 1 个")).toBeTruthy());
  expect(screen.queryByRole("img", { name: "思源黑体（默认）" })).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "搜索字体" }), { target: { value: "没有这种字体" } });
  expect(await screen.findByText("没有匹配的字体")).toBeTruthy();
});

// 场景：一张字体卡片是一个家族：卡片列出已有的字形；点击卡片打开字体详情，四个字形里已有的显示预览条、字重、大小与 SHA-256，
// 缺少的标为未上传，并给出家族名（fontFamily 的取值）、来源与许可证；Esc 关闭后回到列表。
test("点击字体卡片查看各字形的详情", async () => {
  const bold = face("u-2", "bold", { weights: [700], size: 2048, sha256: "ab".repeat(32), preview_url: null });
  fontServer(undefined, [...LIBRARY, font("u-1", "Zed Serif", "我的衬线", { source: "upload", faces: [face("u-1", "regular"), bold] })]);
  render(<FontLibrary />);
  const card = await screen.findByRole("button", { name: "查看字体 我的衬线" });
  expect(card.textContent).toContain("常规 · 粗体");
  fireEvent.click(card);
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("Zed Serif")).toBeTruthy();
  expect(within(dialog).getByText("用户上传")).toBeTruthy();
  expect(within(dialog).getByRole("listitem", { name: "常规" }).textContent).toContain("字重 400 · 1 KB");
  const boldItem = within(dialog).getByRole("listitem", { name: "粗体" });
  expect(boldItem.textContent).toContain("字重 700 · 2 KB");
  expect(boldItem.textContent).toContain(`SHA-256 ${"ab".repeat(32)}`);
  expect(within(dialog).getByRole("listitem", { name: "斜体" }).textContent).toContain("未上传，渲染时由浏览器合成");
  expect(within(dialog).getByRole("listitem", { name: "粗斜体" }).textContent).toContain("未上传");
  fireEvent.keyDown(dialog, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog") === null).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "查看字体 马善政毛笔楷书" }));
  expect((await screen.findByRole("dialog")).textContent).toContain("预装 · OFL-1.1");
});

// 场景：服务端还是字体卡片之前的版本（条目没有 faces 与 license），字体库页面照常列出字体并打开详情，不会因读取
// `faces` 抛错而让整个界面变成黑屏。
test("早于字体卡片的服务端也能打开字体库", async () => {
  const { faces: _faces, license: _license, ...old } = LIBRARY[1]!;
  fetchMock.mockImplementation((async (_input: RequestInfo | URL) => Response.json([old])) as typeof fetch);
  render(<FontLibrary />);
  const card = await screen.findByRole("button", { name: "查看字体 马善政毛笔楷书" });
  expect(card.textContent).toContain("常规");
  fireEvent.click(card);
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByRole("listitem", { name: "常规" }).textContent).toContain("字重 400 · 1 KB");
  expect(within(dialog).getByText("预装")).toBeTruthy();
});

// 场景：系统默认字体在本机没有安装文件时，详情仍展示常规字形与元数据，但不提供会 404 的字样按钮。
test("系统字体文件不在本机时不打开字样", async () => {
  fontServer();
  render(<FontLibrary />);
  fireEvent.click(await screen.findByRole("button", { name: "查看字体 思源黑体（默认）" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByRole("listitem", { name: "常规" }).textContent).toContain("随系统安装");
  expect(within(dialog).queryByRole("button", { name: "查看常规字样" })).toBeNull();
  expect(within(dialog).getByText("本机未安装该字体文件，无法查看字样。")).toBeTruthy();
});

// 场景：在字体详情里点击已有的字形打开它的字样——读取这个字形的字体文件（file_url），用它排出标题、字母数字与字号阶梯，示例句可修改；
// 读取失败时提示并可重试；缺少的字形不能点击；返回后回到详情并移除注册的字体。
test("点击字形查看它的字样", async () => {
  const loads: string[] = [];
  const released: string[] = [];
  let failures = 1;
  const loader: FaceLoader = async (url) => {
    loads.push(url);
    if (failures-- > 0) throw new Error("bad font");
    return { family: "imv-specimen-test", release: () => released.push(url) };
  };
  const bold = face("u-2", "bold", { weights: [700] });
  fontServer(undefined, [...LIBRARY, font("u-1", "Zed Serif", "我的衬线", { source: "upload", faces: [face("u-1", "regular"), bold] })]);
  render(<FontLibrary loader={loader} />);
  fireEvent.click(await screen.findByRole("button", { name: "查看字体 我的衬线" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).queryByRole("button", { name: "查看斜体字样" }) === null).toBe(true);
  fireEvent.click(within(dialog).getByRole("button", { name: "查看粗体字样" }));
  expect((await screen.findByRole("alert")).textContent).toContain("字体文件读取失败");
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  const specimen = await screen.findByLabelText("字样");
  expect(loads.map((url) => new URL(url).pathname)).toEqual(["/api/templates/fonts/u-2", "/api/templates/fonts/u-2"]);
  expect(specimen.style.fontFamily).toContain("imv-specimen-test");
  expect(specimen.textContent).toContain("我的衬线，粗体");
  expect(specimen.textContent).toContain("ABCDEFGHIJKLMNOPQRSTUVWXYZ");
  expect(within(specimen).getAllByText("我能吞下玻璃而不伤身体。")).toHaveLength(11);
  fireEvent.change(screen.getByRole("textbox", { name: "示例文字" }), { target: { value: "你好 Hello" } });
  expect(within(specimen).getAllByText("你好 Hello")).toHaveLength(11);
  fireEvent.click(screen.getByRole("button", { name: "返回字体详情" }));
  expect(await screen.findByRole("button", { name: "查看常规字样" })).toBeTruthy();
  expect(released.map((url) => new URL(url).pathname)).toEqual(["/api/templates/fonts/u-2"]);
});

// 场景：字体库读取失败（服务端不可用）——显示原因与重试，重试成功后列出字体。
test("字体库读取失败时可以重试", async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "字体库暂时不可用" }), { status: 503 }));
  fontServer();
  render(<FontLibrary />);
  expect((await screen.findByRole("alert")).textContent).toContain("字体库暂时不可用");
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(await screen.findByText("共 2 个字体")).toBeTruthy();
});

// 场景：在字体库页面上传字体——浏览器画预览条后上传，成功后列表刷新并出现新字体（标为用户上传）。
test("字体库页面可以上传字体", async () => {
  const server = fontServer(() => Response.json(UPLOADED, { status: 201 }));
  render(<FontLibrary previewer={async () => new Blob(["png"], { type: "image/png" })} />);
  await screen.findByText("共 2 个字体");
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [uploadFile()] } });
  expect(await screen.findByText("共 3 个字体")).toBeTruthy();
  expect(screen.getByText("用户上传")).toBeTruthy();
  expect(server.uploads[0]!.get("label")).toBe("我的字体");
});

// 场景：服务端回答 200（这个文件本来就在库里，例如列表读到之后别人刚传过）——提示已在字体库里，而不是「已添加」。
test("字体库页面上传已在库里的文件时说明已在库里", async () => {
  fontServer(() => Response.json(UPLOADED, { status: 200 }));
  render(<><Toaster /><FontLibrary previewer={async () => null} /></>);
  await screen.findByText("共 2 个字体");
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [uploadFile()] } });
  expect(await screen.findByText("字体已在字体库里：Zed Display")).toBeTruthy();
  expect(screen.queryByText("已添加字体：Zed Display")).toBeNull();
});

// 场景：上传时自动修过文件——成功提示里说明修了什么，用户不会以为上传的字节没有变化。
test("字体库页面说明上传时自动修复了字体", async () => {
  fontServer(() => Response.json(UPLOADED, { status: 201 }));
  render(<><Toaster /><FontLibrary previewer={async () => null} /></>);
  await screen.findByText("共 2 个字体");
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [fontFile("fixed.ttf", [english("Zed Display")], [["cvt ", new Uint8Array(0)]])] } });
  expect(await screen.findByText(/已自动修复字体文件：移除了空的「cvt」表/)).toBeTruthy();
});

// 场景：上传被服务端拒绝（同一个文件已在库里）——原因显示在页面上，列表不变。
test("字体库页面展示上传被拒绝的原因", async () => {
  fontServer(() => Response.json({ detail: "这个字体文件已经在字体库里了：我的字体（Zed Display）" }, { status: 422 }));
  render(<FontLibrary previewer={async () => null} />);
  await screen.findByText("共 2 个字体");
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [new File(["x"], "zed.ttf")] } });
  expect((await screen.findByRole("alert")).textContent).toContain("这个字体文件已经在字体库里了：我的字体（Zed Display）");
  expect(screen.getByText("共 2 个字体")).toBeTruthy();
});

// 场景：左侧工作空间导航有「字体库」入口；打开后显示页头与全部字体，离开再回来重新读取字体库。
test("侧栏可以进入字体库页面", async () => {
  const server = fontServer();
  render(<HomePage />);
  fireEvent.click(screen.getByRole("button", { name: "字体库" }));
  expect(screen.getByRole("heading", { name: "字体库" })).toBeTruthy();
  expect(await screen.findByText("共 2 个字体")).toBeTruthy();
  const listed = () => fetchMock.mock.calls.filter(([url]) => new URL(String(url)).pathname === "/api/templates/fonts").length;
  expect(listed()).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "主页" }));
  await waitFor(() => expect(screen.queryByText("共 2 个字体")).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "字体库" }));
  await screen.findByText("共 2 个字体");
  expect(listed()).toBe(2);
  expect(server.uploads).toEqual([]);
});
