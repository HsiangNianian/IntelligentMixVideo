/** 字体选择器与字体库页面：展示实时字体库、搜索、选择，以及上传自己的字体（显示名取字体自己的名称、浏览器读不了、服务端拒绝与成功后立即选中）；执行 bun run test，不访问网络。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ParameterControl } from "@/components/ParameterControl";
import type { FontInfo } from "@/features/fonts/api";
import { FontLibrary } from "@/features/fonts/FontLibrary";
import HomePage from "@/pages/HomePage";
import { FontPicker } from "@/features/fonts/FontPicker";
import { english, fontFile } from "./font-files";
import { fetchMock } from "./setup";

const font = (id: string, family: string, label: string, extra: Partial<FontInfo> = {}): FontInfo => ({
  id, family, label, style: "sample style", weights: [400], source: "builtin", size: 1000,
  file_url: `/api/templates/fonts/${id}`, preview_url: `/api/templates/fonts/${id}/preview`, ...extra,
});
const LIBRARY = [
  font("noto-sans-cjk", "Noto Sans CJK SC", "思源黑体（默认）", { source: "system", file_url: null, size: 0, style: "default clean Hei" }),
  font("ma-shan-zheng", "Ma Shan Zheng", "马善政毛笔楷书", { style: "brush calligraphy" }),
];

const UPLOADED = font("u-1", "Zed Display", "我的字体", { source: "upload" });
/** 文件名是一串哈希、英文家族名 Zed Display、简体中文名“我的字体”的字体文件。 */
const uploadFile = () => fontFile("0b7027d34f9546b7993e082b48c22401.ttf", [english("Zed Display"), [3, 1, 0x0804, 1, "我的字体"]]);

/** 字体库接口：GET 返回当前列表，POST 按 `onUpload` 的结果响应；记录上传请求的表单。 */
function fontServer(onUpload?: (form: FormData) => Response) {
  const library = [...LIBRARY];
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

// 场景：浏览器读不了这个字体文件（预览条画不出来）——不发上传请求，提示选择有效字体。
test("无法读取的字体文件不会被上传", async () => {
  const server = fontServer(() => Response.json(UPLOADED, { status: 201 }));
  const changes: string[] = [];
  await open((family) => changes.push(family), async () => { throw new Error("bad font"); });
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [fontFile("broken.ttf", [english("Broken")])] } });
  expect((await screen.findByRole("alert")).textContent).toContain("无法读取这个字体文件");
  expect(server.uploads).toEqual([]);
  expect(changes).toEqual([]);
});

// 场景：读不出字体名称（不是 TrueType/OpenType）——不画预览条、不用文件名当显示名，由服务端判断并展示它给出的原因。
test("读不出名称的文件交给服务端判断", async () => {
  const server = fontServer(() => Response.json({ detail: "只支持 TrueType / OpenType 字体文件（.ttf、.otf）" }, { status: 422 }));
  const drawn: string[] = [];
  await open(() => undefined, async (_file, text) => { drawn.push(text); return null; });
  fireEvent.change(screen.getByLabelText("选择字体文件"), { target: { files: [new File(["x"], "notes.ttf")] } });
  expect((await screen.findByRole("alert")).textContent).toContain("只支持 TrueType / OpenType");
  expect(drawn).toEqual([]);
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
