/** 字体测试用的最小 sfnt 文件：只有一张 name 表，足够读出名称；浏览器画预览条的部分在测试里另行替换。 */

/** 一条 name 表记录：平台、编码、语言、名称 ID 与文字（Unicode/Windows 平台按 UTF-16BE 写入，其余按单字节写入）。 */
export type NameEntry = [platform: number, encoding: number, language: number, nameId: number, text: string];

/** Windows 美国英语的家族名称记录。 */
export const english = (text: string, nameId = 1): NameEntry => [3, 1, 0x0409, nameId, text];

/** 只含给定 name 记录的 TrueType 字节。 */
export function fontBytes(entries: NameEntry[]): Uint8Array<ArrayBuffer> {
  const encoded = entries.map(([platform, , , , text]) => platform === 0 || platform === 3
    ? Uint8Array.from([...text].flatMap((char) => {
      const units = char.length === 2 ? [char.charCodeAt(0), char.charCodeAt(1)] : [char.charCodeAt(0)];
      return units.flatMap((unit) => [unit >> 8, unit & 0xff]);
    }))
    : Uint8Array.from([...text].map((char) => char.charCodeAt(0) & 0xff)));
  const strings = 6 + 12 * entries.length;
  const tableLength = strings + encoded.reduce((total, bytes) => total + bytes.length, 0);
  const bytes = new Uint8Array(28 + tableLength);
  const view = new DataView(bytes.buffer);
  view.setUint32(0, 0x00010000);
  view.setUint16(4, 1);
  bytes.set([0x6e, 0x61, 0x6d, 0x65], 12);
  view.setUint32(20, 28);
  view.setUint32(24, tableLength);
  view.setUint16(28 + 2, entries.length);
  view.setUint16(28 + 4, strings);
  let offset = 0;
  entries.forEach(([platform, encoding, language, nameId], index) => {
    const record = 28 + 6 + 12 * index;
    [platform, encoding, language, nameId, encoded[index]!.length, offset].forEach((value, field) => view.setUint16(record + 2 * field, value));
    bytes.set(encoded[index]!, 28 + strings + offset);
    offset += encoded[index]!.length;
  });
  return bytes;
}

/** 以 `fileName` 命名、名称为 `entries` 的字体文件。 */
export function fontFile(fileName: string, entries: NameEntry[]): File {
  return new File([fontBytes(entries)], fileName, { type: "font/ttf" });
}
