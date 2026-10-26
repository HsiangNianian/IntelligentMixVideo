/** 字体测试用的最小 sfnt 文件：一张 name 表与调用方给出的额外表，足够读出名称或构造上传时的目录级修复场景；浏览器画预览条的部分在测试里另行替换。 */

/** 一条 name 表记录：平台、编码、语言、名称 ID 与文字（Unicode/Windows 平台按 UTF-16BE 写入，其余按单字节写入）。 */
export type NameEntry = [platform: number, encoding: number, language: number, nameId: number, text: string];

/** 一张额外登记的表：标签与表数据；数据为空即零长度表（做字、裁剪或合并工具导出的常见缺陷）。 */
export type ExtraTable = [tag: string, data: Uint8Array];

/** Windows 美国英语的家族名称记录。 */
export const english = (text: string, nameId = 1): NameEntry => [3, 1, 0x0409, nameId, text];

/** 表标签的四字节数。 */
function tagOf(tag: string): number {
  return [...tag.padEnd(4, " ").slice(0, 4)].reduce((value, char) => value * 256 + char.charCodeAt(0), 0);
}

/** sfnt 校验和：按大端 32 位字求和，末尾补零到四字节。 */
function checksum(bytes: Uint8Array): number {
  let sum = 0;
  for (let offset = 0; offset < bytes.length; offset += 4) {
    sum = (sum + bytes[offset]! * 0x1000000 + (bytes[offset + 1] ?? 0) * 0x10000 + (bytes[offset + 2] ?? 0) * 0x100 + (bytes[offset + 3] ?? 0)) % 0x100000000;
  }
  return sum;
}

/** name 表的字节：记录与字符串。 */
function nameTable(entries: NameEntry[]): Uint8Array<ArrayBuffer> {
  const encoded = entries.map(([platform, , , , text]) => platform === 0 || platform === 3
    ? Uint8Array.from([...text].flatMap((char) => {
      const units = char.length === 2 ? [char.charCodeAt(0), char.charCodeAt(1)] : [char.charCodeAt(0)];
      return units.flatMap((unit) => [unit >> 8, unit & 0xff]);
    }))
    : Uint8Array.from([...text].map((char) => char.charCodeAt(0) & 0xff)));
  const strings = 6 + 12 * entries.length;
  const table = new Uint8Array(strings + encoded.reduce((total, bytes) => total + bytes.length, 0));
  const view = new DataView(table.buffer);
  view.setUint16(2, entries.length);
  view.setUint16(4, strings);
  let offset = 0;
  entries.forEach(([platform, encoding, language, nameId], index) => {
    const record = 6 + 12 * index;
    [platform, encoding, language, nameId, encoded[index]!.length, offset].forEach((value, field) => view.setUint16(record + 2 * field, value));
    table.set(encoded[index]!, strings + offset);
    offset += encoded[index]!.length;
  });
  return table;
}

/** 只含 name 表与给定额外表的 TrueType 字节；表按标签顺序登记，数据依次排在目录之后。 */
export function fontBytes(entries: NameEntry[], extras: ExtraTable[] = []): Uint8Array<ArrayBuffer> {
  const tables = [{ tag: "name", data: nameTable(entries) }, ...extras.map(([tag, data]) => ({ tag, data }))]
    .sort((left, right) => tagOf(left.tag) - tagOf(right.tag));
  const header = 12 + 16 * tables.length;
  const bytes = new Uint8Array(header + tables.reduce((total, table) => total + ((table.data.length + 3) & ~3), 0));
  const view = new DataView(bytes.buffer);
  view.setUint32(0, 0x00010000);
  const widest = 2 ** Math.floor(Math.log2(tables.length));
  view.setUint16(4, tables.length);
  view.setUint16(6, widest * 16);
  view.setUint16(8, Math.log2(widest));
  view.setUint16(10, tables.length * 16 - widest * 16);
  let offset = header;
  const offsets: number[] = [];
  tables.forEach((table, index) => {
    const at = 12 + 16 * index;
    view.setUint32(at, tagOf(table.tag));
    view.setUint32(at + 8, offset);
    view.setUint32(at + 12, table.data.length);
    bytes.set(table.data, offset);
    offsets[index] = offset;
    offset += (table.data.length + 3) & ~3;
  });
  // 表校验和按约定写入（head 的 checkSumAdjustment 此时是 0），有 head 时再补上整份文件的调整值，让测试字体本身自洽。
  tables.forEach((table, index) => {
    const start = offsets[index]!;
    view.setUint32(12 + 16 * index + 4, checksum(bytes.subarray(start, start + table.data.length)));
  });
  const headIndex = tables.findIndex((table) => table.tag === "head");
  if (headIndex >= 0 && tables[headIndex]!.data.length >= 12) view.setUint32(offsets[headIndex]! + 8, (0xb1b0afba - checksum(bytes)) >>> 0);
  return bytes;
}

/** 以 `fileName` 命名、名称为 `entries`（可带额外表）的字体文件。 */
export function fontFile(fileName: string, entries: NameEntry[], extras: ExtraTable[] = []): File {
  return new File([fontBytes(entries, extras)], fileName, { type: "font/ttf" });
}
