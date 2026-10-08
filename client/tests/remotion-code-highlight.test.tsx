/** 字效代码卡片的高亮与诊断标注回归；不连接真实服务端、类型检查或浏览器。 */
import { expect, spyOn, test } from "bun:test";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CodeBlock } from "@/features/remotion_templates/CodeBlock";
import { highlight } from "@/features/remotion_templates/codeHighlight";
import { VersionCard } from "@/features/remotion_templates/VersionCard";
import type { Diagnostic } from "@/features/remotion_templates/model";
import { remotionVersion } from "./remotion-fixtures";
import { remotionServer } from "./remotion-server";
import { fetchMock } from "./setup";

/** 构造一条带一基行列的诊断，与服务端 CodeDiagnostic 字段一致。 */
function diagnostic(
  line: number,
  severity: Diagnostic["severity"],
  overrides: Partial<Diagnostic> = {},
): Diagnostic {
  return {
    source: "lsp",
    severity,
    message: severity === "error" ? "类型不匹配" : "该变量已声明但未被读取",
    file: "Template.tsx",
    code: severity === "error" ? "2322" : "6133",
    range: {
      start: { line: line - 1, character: 4 },
      end: { line: line - 1, character: 10 },
    },
    ...overrides,
  };
}

/** 分词结果拼接后必须与输入完全一致，否则高亮会丢失或重复字符。 */
test("高亮片段无损拼接并识别关键字、字符串与注释", () => {
  const source = 'const title: string = "今日灵感"; // 标题\n';
  const tokens = highlight(source);
  expect(tokens.map((token) => token.text).join("")).toBe(source);
  const kinds = new Map(tokens.map((token) => [token.text, token.kind]));
  expect(kinds.get("const")).toBe("keyword");
  expect(kinds.get('"今日灵感"')).toBe("string");
  expect(kinds.get("// 标题")).toBe("comment");
});

// 未闭合的字符串与块注释不得吞掉剩余源码，避免整块误着色。
test("高亮对未闭合字面量与空输入保持有界", () => {
  expect(highlight("")).toEqual([]);
  expect(highlight('const a = "unterminated').map((t) => t.text).join("")).toBe(
    'const a = "unterminated',
  );
  expect(highlight("/* open").map((t) => t.text).join("")).toBe("/* open");
});

// JSX 标签与属性名分别着色，普通标识符保持素色。
test("高亮区分 JSX 标签与属性名", () => {
  const tokens = highlight('<div className="box">{text}</div>');
  const kinds = new Map(tokens.map((token) => [token.text, token.kind]));
  expect(kinds.get("<div")).toBe("tag");
  expect(kinds.get("className")).toBe("attribute");
  expect(kinds.get('"box"')).toBe("string");
  expect(kinds.get("</div")).toBe("tag");
  // 空白与 `=` 必须原样保留，否则拼接会丢字符。
  expect(tokens.map((token) => token.text).join("")).toBe(
    '<div className="box">{text}</div>',
  );
});

// 诊断按一基行号落到对应行，error 行有标记与完整消息。
test("代码块按行标注诊断并提供清单", () => {
  render(
    <CodeBlock
      code={"const a = 1;\nconst b = 2;\nconst c = 3;"}
      diagnostics={[diagnostic(2, "error"), diagnostic(3, "warning")]}
      fileName="Export.tsx"
    />,
  );
  expect(screen.getByLabelText("第 2 行：error")).toBeTruthy();
  expect(screen.getByLabelText("第 3 行：warning")).toBeTruthy();
  expect(screen.queryByLabelText("第 1 行：error")).toBeNull();
  const items = screen.getAllByRole("listitem");
  expect(items).toHaveLength(2);
  expect(items[0]!.textContent).toContain("第 2 行 第 5 列");
  expect(items[0]!.textContent).toContain("TS2322");
  expect(items[0]!.textContent).toContain("类型不匹配");
});

// 点击清单条目滚动到对应行，且不触发版本预览。
test("点击诊断条目滚动到对应行且不改变版本选择", () => {
  const scrolled: number[] = [];
  const original = HTMLElement.prototype.scrollIntoView;
  if (!original) HTMLElement.prototype.scrollIntoView = () => {};
  const scroll = spyOn(
    HTMLElement.prototype,
    "scrollIntoView",
  ).mockImplementation(function (this: HTMLElement) {
    scrolled.push(Number(this.dataset.line ?? 0));
  });
  try {
    render(
      <CodeBlock
        code={"a\nb\nc"}
        diagnostics={[diagnostic(3, "warning")]}
        fileName="Export.tsx"
      />,
    );
    expect(scrolled).toEqual([]);
    fireEvent.click(screen.getByText(/该变量已声明但未被读取/));
    expect(scrolled).toEqual([3]);
  } finally {
    scroll.mockRestore();
    if (!original)
      Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
  }
});

// 无位置信息的诊断进入清单但不标记任何行，避免误导。
test("缺少范围的诊断不标记行", () => {
  const unlocated: Diagnostic = {
    source: "contract",
    severity: "error",
    message: "默认参数不符合 Schema",
    field: "title",
  };
  render(
    <CodeBlock code={"a"} diagnostics={[unlocated]} fileName="Export.tsx" />,
  );
  expect(screen.queryByLabelText(/第 1 行：error/)).toBeNull();
  expect(screen.getByText(/文件级 · error · contract/)).toBeTruthy();
});

// 展开版本卡片时读取代码与诊断；诊断失败只降级诊断区。
test("版本卡片展开时读取诊断并显示计数", async () => {
  // 测试替身返回单行 Export.tsx，诊断必须落在真实存在的第 1 行。
  remotionServer((path) =>
    path.endsWith("/diagnostics")
      ? Response.json({ passed: false, diagnostics: [diagnostic(1, "warning")] })
      : undefined,
  );
  render(
    <VersionCard
      version={remotionVersion()}
      selected={false}
      latest={false}
      disabled={false}
      previewDisabled={false}
      onPreview={() => {}}
    />,
  );
  expect(fetchMock).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "展开 V1 代码" }));
  await waitFor(() =>
    expect(screen.getByLabelText("第 1 行：warning")).toBeTruthy(),
  );
  expect(screen.getByText("1 项诊断")).toBeTruthy();
  expect(screen.getByLabelText("模板 TSX 代码")).toBeTruthy();
});

// 诊断读取失败时仍显示代码，并提供显式重试。
test("诊断失败保留代码显示并允许重试", async () => {
  let fail = true;
  remotionServer((path) =>
    path.endsWith("/diagnostics")
      ? fail
        ? new Response(null, { status: 503 })
        : Response.json({ passed: true, diagnostics: [] })
      : undefined,
  );
  render(
    <VersionCard
      version={remotionVersion()}
      selected={false}
      latest={false}
      disabled={false}
      previewDisabled={false}
      onPreview={() => {}}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "展开 V1 代码" }));
  await screen.findByText(/诊断读取失败/);
  await waitFor(() =>
    expect(screen.getByLabelText("模板 TSX 代码").textContent).toContain(
      "今日灵感",
    ),
  );
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "重试诊断" }));
  // 重试成功后提示、重试入口与诊断清单都消失，代码仍可查看。
  await waitFor(() => expect(screen.queryByText(/诊断读取失败/)).toBeNull());
  expect(screen.queryByLabelText("代码诊断")).toBeNull();
  expect(screen.queryByText("1 项诊断")).toBeNull();
  expect(screen.queryByRole("button", { name: "重试诊断" })).toBeNull();
});

// 未保存参数时与复制代码一致：不发起诊断请求。
test("版本卡片在禁用状态不请求诊断", () => {
  remotionServer();
  render(
    <VersionCard
      version={remotionVersion()}
      selected={false}
      latest={false}
      disabled
      previewDisabled={false}
      onPreview={() => {}}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "展开 V1 代码" }));
  expect(
    fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/diagnostics")),
  ).toHaveLength(0);
});
