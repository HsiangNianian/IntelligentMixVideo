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
import { diagnostics } from "@/features/remotion_templates/api";

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
    file: "Export.tsx",
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
test("版本卡片在禁用状态不请求诊断", async () => {
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
  await waitFor(() => expect(screen.getByLabelText("模板 TSX 代码").textContent).toContain("今日灵感"));
});

// 外部文件与越界位置只展示清单，不标到当前导出文件或提供无效跳转。
test("其他文件和越界诊断不标记当前导出源码", () => {
  render(<CodeBlock code={"one\ntwo"} fileName="Export.tsx" diagnostics={[
    diagnostic(1, "error", { file: "Template.tsx", message: "原始组件诊断" }),
    diagnostic(2, "error", { file: "contract.tsx", message: "调用点诊断" }),
    diagnostic(99, "error", { message: "越界诊断" }),
    diagnostic(1, "error", { file: undefined, message: "无文件诊断" }),
  ]} />);
  expect(screen.queryByLabelText(/第 .* 行：/)).toBeNull();
  expect(screen.queryAllByRole("button")).toHaveLength(0);
  expect(screen.getByText(/Template.tsx.*原始组件诊断/)).toBeTruthy();
  expect(screen.getByText(/contract.tsx.*调用点诊断/)).toBeTruthy();
});

// 成功读取后的再次展开复用报告，避免持续生成只读 worker。
test("重新展开卡片复用成功诊断", async () => {
  let signal: AbortSignal | null | undefined;
  let calls = 0;
  remotionServer((path, options) => {
    if (!path.endsWith("/diagnostics")) return;
    calls++;
    signal = options?.signal;
    return Response.json({ passed: false, diagnostics: [diagnostic(1, "warning")] });
  });
  const view = render(<VersionCard version={remotionVersion()} selected={false} latest={false}
    disabled={false} previewDisabled={false} onPreview={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "展开 V1 代码" }));
  await screen.findByText("1 项诊断");
  fireEvent.click(screen.getByRole("button", { name: "展开 V1 代码" }));
  fireEvent.click(screen.getByRole("button", { name: "展开 V1 代码" }));
  expect(calls).toBe(1);
  expect(signal?.aborted).toBe(false);
  view.unmount();
});

// 客户端超时覆盖服务端允许的 600 秒上限；调用方取消仍能立刻中断并清理定时器。
test("慢诊断使用独立超时并支持调用方取消", async () => {
  const timer = spyOn(window, "setTimeout");
  const clear = spyOn(window, "clearTimeout");
  remotionServer((_path, options) => new Promise<Response>((_resolve, reject) => {
    options?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
  }));
  const controller = new AbortController();
  const request = diagnostics("version-1", controller.signal);
  expect(timer.mock.calls.at(-1)?.[1]).toBeGreaterThan(600_000);
  const timerId = timer.mock.results.at(-1)?.value;
  controller.abort();
  await expect(request).rejects.toThrow("Aborted");
  expect(clear.mock.calls.some(([id]) => id === timerId)).toBe(true);
});

// 服务端明确失败但缺少定位详情时不得把空清单呈现为代码检查通过。
test("没有定位信息的失败诊断仍显示未通过", async () => {
  remotionServer((path) => path.endsWith("/diagnostics")
    ? Response.json({ passed: false, diagnostics: [] }) : undefined);
  render(<VersionCard version={remotionVersion()} selected={false} latest={false}
    disabled={false} previewDisabled={false} onPreview={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "展开 V1 代码" }));
  expect(await screen.findByText("代码检查未通过，服务端未提供定位信息。")).toBeTruthy();
});

// 退出卡片时取消慢诊断，释放本地请求监听且不在卸载后写入状态。
test("卸载版本卡片中止未完成的诊断", async () => {
  let signal: AbortSignal | null | undefined;
  remotionServer((path, options) => {
    if (!path.endsWith("/diagnostics")) return;
    signal = options?.signal;
    return new Promise<Response>((_resolve, reject) => {
      signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    });
  });
  const view = render(<VersionCard version={remotionVersion()} selected={false} latest={false}
    disabled={false} previewDisabled={false} onPreview={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "展开 V1 代码" }));
  await screen.findByText("正在检查代码…");
  expect(signal?.aborted).toBe(false);
  view.unmount();
  expect(signal?.aborted).toBe(true);
});
