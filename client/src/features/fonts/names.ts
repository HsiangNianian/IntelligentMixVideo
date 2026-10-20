/**
 * 字体自己的显示名称：从 TrueType/OpenType 的 name 表读出，优先中文名。上传时浏览器用它画预览条，
 * 规则与服务端 `fonts.read_names` 的显示名称一致（服务端只保留文字与之相同的预览条），所以卡片不会再显示文件名。
 */

/** 依次优先的 Windows 中文语言：简体中文（中国、新加坡）、繁体中文（台湾、香港、澳门）。 */
const CHINESE_LANGUAGES = [0x0804, 0x1004, 0x0404, 0x0c04, 0x1404];
/** 家族名称的最大长度（按字符计），与服务端一致。 */
const MAX_LENGTH = 100;
/** 可打印文字：没有控制、格式、代理、私用、未分配字符，也没有空格以外的分隔符（与 Python `str.isprintable` 相同）。 */
const PRINTABLE = /^(?:[^\p{C}\p{Z}]| )+$/u;
/** 解码时丢弃的孤立代理项（与 Python 以 `errors="ignore"` 解码 UTF-16 相同）。 */
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g;

/** name 表里一条可用的家族名称记录。 */
interface NameRecord {
  platform: number;
  language: number;
  nameId: number;
  text: string;
  order: number;
}

/** 按 UTF-16BE 解码一段字节；奇数长度时末尾多出的字节被忽略。 */
function utf16be(view: DataView, start: number, size: number): string {
  let text = "";
  for (let offset = 0; offset + 1 < size; offset += 2) text += String.fromCharCode(view.getUint16(start + offset));
  return text.replace(LONE_SURROGATE, "");
}

/** 显示名称的排序：中文在前，其次 Unicode 平台或美国英语，最后其他 Windows 语言；同一语言里字体家族（16）先于旧式家族（1）。 */
function rank(record: NameRecord): [number, number, number] {
  const chinese = record.platform === 3 ? CHINESE_LANGUAGES.indexOf(record.language) : -1;
  const language = chinese >= 0
    ? chinese
    : record.platform === 0 || record.language === 0x0409 ? CHINESE_LANGUAGES.length : CHINESE_LANGUAGES.length + 1;
  return [language, record.nameId === 16 ? 0 : 1, record.order];
}

/**
 * 字体的显示名称：name 表里 Unicode 编码的家族名称（ID 16 或 1）中按 `rank` 最靠前的一条。
 * 不是 sfnt 字体、文件损坏或没有可用的 Unicode 名称时返回 null（不画预览条，名称交给服务端决定）。
 */
export function fontDisplayName(buffer: ArrayBuffer): string | null {
  try {
    const view = new DataView(buffer);
    const magic = view.getUint32(0);
    if (magic !== 0x00010000 && magic !== 0x4f54544f && magic !== 0x74727565) return null;
    let table: { offset: number; length: number } | null = null;
    for (let index = 0, tables = view.getUint16(4); index < tables; index += 1) {
      const entry = 12 + 16 * index;
      if (view.getUint32(entry) === 0x6e616d65) {
        table = { offset: view.getUint32(entry + 8), length: view.getUint32(entry + 12) };
        break;
      }
    }
    if (!table || table.offset > buffer.byteLength) return null;
    // name 表超出文件末尾时只读到文件结尾，与服务端按切片读取一致。
    const length = Math.min(table.length, buffer.byteLength - table.offset);
    const name = new DataView(buffer, table.offset, length);
    const count = name.getUint16(2);
    const strings = name.getUint16(4);
    const records: NameRecord[] = [];
    for (let index = 0; index < count; index += 1) {
      const entry = 6 + 12 * index;
      const [platform, encoding, language, nameId, size, start] = [0, 2, 4, 6, 8, 10].map((field) => name.getUint16(entry + field));
      if ((nameId !== 1 && nameId !== 16) || !(platform === 0 || (platform === 3 && [0, 1, 10].includes(encoding)))) continue;
      if (strings + start + size > length) continue;
      const text = utf16be(name, strings + start, size).trim();
      if (text && [...text].length <= MAX_LENGTH && PRINTABLE.test(text)) records.push({ platform, language, nameId, text, order: index });
    }
    const best = records.sort((left, right) => {
      const [a, b] = [rank(left), rank(right)];
      return a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
    })[0];
    return best?.text ?? null;
  } catch (error) {
    if (error instanceof RangeError) return null;
    throw error;
  }
}
