/**
 * 字效代码卡片的展示用 TSX 高亮分词器。
 *
 * 责任边界：只把源码切成着色片段，不解析完整语法、不做类型检查、不参与验收，
 * 也不进入隔离预览包。服务端 TypeScript 语言服务的诊断才是真实结论，
 * 这里的高亮仅帮助阅读，任何分词错误都只影响颜色。
 *
 * 数据流：`highlight()` 单遍扫描源码 → `{ text, kind }[]` 片段，
 * 由 `CodeBlock` 按主题语义令牌映射为 class。
 */

/** 片段类别；`plain` 表示无需着色的普通文本。 */
export type TokenKind =
  | "plain"
  | "comment"
  | "string"
  | "number"
  | "keyword"
  | "tag"
  | "attribute";

/** 一段源码及其着色类别，拼接后与输入完全一致。 */
export interface Token {
  text: string;
  kind: TokenKind;
}

/** 展示用的有界输入；超出部分按普通文本输出，避免超长产物拖慢渲染。 */
const MAX_TOKENS = 20_000;

const KEYWORDS = new Set([
  "as",
  "async",
  "await",
  "break",
  "case",
  "catch",
  "class",
  "const",
  "continue",
  "declare",
  "default",
  "delete",
  "do",
  "else",
  "enum",
  "export",
  "extends",
  "false",
  "finally",
  "for",
  "from",
  "function",
  "if",
  "implements",
  "import",
  "in",
  "instanceof",
  "interface",
  "let",
  "new",
  "null",
  "of",
  "return",
  "satisfies",
  "static",
  "super",
  "switch",
  "this",
  "throw",
  "true",
  "try",
  "type",
  "typeof",
  "undefined",
  "var",
  "void",
  "while",
  "yield",
]);

/** 标识符字符；用于确认关键字与属性名边界，覆盖常见 Unicode 字母。 */
function isIdentifierStart(char: string): boolean {
  return /[A-Za-z_$]/u.test(char);
}

/** 标识符后续字符，允许数字与常见 Unicode 字母/数字。 */
function isIdentifierPart(char: string): boolean {
  return /[A-Za-z0-9_$]/u.test(char);
}

/**
 * 把源码切成展示用片段。
 *
 * 单遍扫描，不回溯；无法识别的字符按 `plain` 输出。
 * 注释、字符串、模板字符串与正则字面量只做词法识别，不进入语义判断。
 */
export function highlight(source: string): Token[] {
  const tokens: Token[] = [];
  let plain = "";
  let index = 0;

  /** 冲刷已累积的普通文本，保持片段顺序与原文一致。 */
  function flush() {
    if (plain) {
      tokens.push({ text: plain, kind: "plain" });
      plain = "";
    }
  }
  /** 追加一个已确定类别的片段。 */
  function push(text: string, kind: TokenKind) {
    flush();
    tokens.push({ text, kind });
  }

  while (index < source.length) {
    if (tokens.length >= MAX_TOKENS) {
      plain += source.slice(index);
      break;
    }
    const rest = source.slice(index);
    const char = source[index]!;

    // 行注释与块注释。
    if (rest.startsWith("//")) {
      const end = source.indexOf("\n", index);
      const stop = end === -1 ? source.length : end;
      push(source.slice(index, stop), "comment");
      index = stop;
      continue;
    }
    if (rest.startsWith("/*")) {
      const end = source.indexOf("*/", index + 2);
      const stop = end === -1 ? source.length : end + 2;
      push(source.slice(index, stop), "comment");
      index = stop;
      continue;
    }

    // 字符串与模板字符串；模板字符串内的 `${}` 不单独着色，避免把表达式误判为文本。
    if (char === '"' || char === "'" || char === "`") {
      let cursor = index + 1;
      while (cursor < source.length) {
        if (source[cursor] === "\\") {
          cursor += 2;
          continue;
        }
        if (source[cursor] === char) {
          cursor += 1;
          break;
        }
        if (char !== "`" && source[cursor] === "\n") break;
        cursor += 1;
      }
      push(source.slice(index, cursor), "string");
      index = cursor;
      continue;
    }

    // JSX 标签起始：`<div`、`</div`、`<>`；闭合标签整体作为一个片段。
    if (char === "<" && /^<\/?[A-Za-z_$>]/u.test(rest.slice(0, 3))) {
      const name = rest.match(/^<\/?[A-Za-z_$][A-Za-z0-9_$]*/u);
      if (name) {
        push(name[0], "tag");
        index += name[0].length;
        // 闭合标签到此结束；起始标签继续读取属性名，直到 `>`。
        if (!name[0].startsWith("</")) {
          while (index < source.length && source[index] !== ">") {
            const attribute = source.slice(index);
            // 空白原样累积，保证片段拼接无损。
            const space = attribute.match(/^\s+/u);
            if (space) {
              plain += space[0];
              index += space[0].length;
              continue;
            }
            const named = attribute.match(/^[A-Za-z_$][A-Za-z0-9_$:-]*/u);
            if (named) {
              push(named[0], "attribute");
              index += named[0].length;
              continue;
            }
            // 属性值按字符串着色，其余字符（`=`、`/`、`{` 等）保持素色。
            const value = attribute.match(/^(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')/u);
            if (value) {
              push(value[0], "string");
              index += value[0].length;
              continue;
            }
            plain += source[index];
            index += 1;
          }
        }
        continue;
      }
    }

    // 数字字面量；只在标识符边界起效，避免切碎 `foo1` 这类名字。
    if (/[0-9]/u.test(char) && !isIdentifierPart(plain.slice(-1))) {
      const match = rest.match(/^(?:0[xXbBoO][0-9a-fA-F_]+|[0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][+-]?[0-9]+)?)/u);
      if (match) {
        push(match[0], "number");
        index += match[0].length;
        continue;
      }
    }

    // 关键字；要求前后都不是标识符字符。
    if (isIdentifierStart(char) && !isIdentifierPart(plain.slice(-1))) {
      const match = rest.match(/^[A-Za-z_$][A-Za-z0-9_$]*/u);
      if (match && KEYWORDS.has(match[0])) {
        push(match[0], "keyword");
        index += match[0].length;
        continue;
      }
    }

    plain += char;
    index += 1;
  }
  flush();
  return tokens;
}

/** 每个片段类别对应的主题语义类名；不硬编码色值，明暗主题都可用。 */
export const TOKEN_CLASS: Record<TokenKind, string> = {
  plain: "",
  comment: "text-muted-foreground/70 italic",
  string: "text-emerald-700 dark:text-emerald-400",
  number: "text-amber-700 dark:text-amber-400",
  keyword: "text-primary",
  tag: "text-sky-700 dark:text-sky-400",
  attribute: "text-violet-700 dark:text-violet-400",
};
