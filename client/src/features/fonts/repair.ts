/**
 * 上传前的字体目录级修复：浏览器（OTS）会拒绝含零长度表的字体，而不少做字、裁剪或合并工具导出的中文字体会带上长度为 0 的
 * `cvt `、`fpgm`、`DSIG` 等表，这类文件在系统里能安装、浏览器却解不出来。这里只改表目录与校验和——删除没有数据的表、
 * 重排目录并补正二分查找字段、按实际数据重算各表校验和与 `head` 的 checkSumAdjustment、裁掉尾部垃圾；`glyf`、`cmap`、
 * `name` 等表的数据一个字节都不动，所以不需要字体库。
 * 认出的结构问题（目录或表超出文件末尾、表与目录重叠、必需表为空、`loca` 与 `glyf` 不自洽）不猜着修，交给调用方拒绝并说明原因。
 * 修过的文件再修一次结果不变（幂等），同一份原文件总是得到同一份修复结果，按内容去重不会漂移。
 */

/** 认得的 sfnt 版本：TrueType、OpenType/CFF 与旧式 `true` 标记；字体集合（`ttcf`）与不认识的格式原样返回，交给浏览器判断。 */
const SFNT_VERSIONS = [0x00010000, 0x4f54544f, 0x74727565];
/** 表标签。 */
const TAG = {
  head: 0x68656164, hhea: 0x68686561, maxp: 0x6d617870, hmtx: 0x686d7478, cmap: 0x636d6170,
  name: 0x6e616d65, glyf: 0x676c7966, loca: 0x6c6f6361, cff: 0x43464620, cff2: 0x43464632,
} as const;
/** 长度为 0 就说明字体真坏了、不能靠删表变成可用的字体：删掉它们只会得到另一份解不出来的文件。 */
const REQUIRED_TABLES = new Set<number>([TAG.head, TAG.hhea, TAG.maxp, TAG.hmtx, TAG.cmap, TAG.name, TAG.glyf, TAG.loca, TAG.cff, TAG.cff2]);
/** `head` 表的固定长度；其中的 checkSumAdjustment 在表内偏移 8。 */
const HEAD_LENGTH = 54;
const CHECKSUM_ADJUSTMENT = 8;
/** 整份文件的校验和与 checkSumAdjustment 之和应为这个常数。 */
const CHECKSUM_MAGIC = 0xb1b0afba;

/** 表目录里的一项。 */
interface TableEntry {
  tag: number;
  checksum: number;
  offset: number;
  length: number;
}

/** 修复结果：不用修（或不是认得的 sfnt）、已修复（带说明），或认出但无法安全修复的结构问题。 */
export type FontRepair =
  | { kind: "unchanged" }
  | { kind: "repaired"; bytes: ArrayBuffer; repairs: string[] }
  | { kind: "damaged"; detail: string };

/**
 * 修一份字体字节，见文件头。`bytes` 是浏览器读到的原始文件内容；结果里的 `bytes` 是可以直接上传的修复版本，
 * 未修时调用方继续用原文件（不重写，免得无谓地改变摘要）。
 */
export function repairFont(bytes: ArrayBuffer): FontRepair {
  const view = new DataView(bytes);
  // 太短或不是认得的 sfnt：不猜，原样返回，让浏览器的解码检查给出结论。
  if (bytes.byteLength < 12 || !SFNT_VERSIONS.includes(view.getUint32(0))) return { kind: "unchanged" };
  const source = new Uint8Array(bytes);

  const count = view.getUint16(4);
  if (count === 0) return { kind: "damaged", detail: "字体没有表目录" };
  const directoryEnd = 12 + 16 * count;
  if (directoryEnd > bytes.byteLength) return { kind: "damaged", detail: "表目录超出文件末尾" };

  const entries: TableEntry[] = [];
  for (let index = 0; index < count; index += 1) {
    const at = 12 + 16 * index;
    const entry = { tag: view.getUint32(at), checksum: view.getUint32(at + 4), offset: view.getUint32(at + 8), length: view.getUint32(at + 12) };
    if (entry.length && entry.offset < directoryEnd) return { kind: "damaged", detail: `「${tagName(entry.tag)}」表与表目录重叠` };
    if (entry.length && entry.offset + entry.length > bytes.byteLength) return { kind: "damaged", detail: `「${tagName(entry.tag)}」表超出文件末尾` };
    entries.push(entry);
  }

  // 必需表为空说明内容已经丢了，删掉它并不能让字体变得能用。
  const empty = entries.filter((entry) => entry.length === 0);
  const lost = empty.find((entry) => REQUIRED_TABLES.has(entry.tag));
  if (lost) return { kind: "damaged", detail: `「${tagName(lost.tag)}」表是空的` };
  const head = entries.find((entry) => entry.tag === TAG.head);
  if (head && head.length && head.length < HEAD_LENGTH) return { kind: "damaged", detail: "head 表不完整" };

  const broken = glyphsProblem(view, entries);
  if (broken) return { kind: "damaged", detail: broken };

  const kept = entries.filter((entry) => entry.length > 0).sort((left, right) => left.tag - right.tag);
  if (kept.length === 0) return { kind: "damaged", detail: "字体没有可用的表" };

  // 原文件本身是否自洽：这三项用来说明“原文件哪一处有问题”，与我们自己重建目录造成的字段变化区分开。
  const fields = expectedHeader(count);
  const headerWrong = view.getUint16(6) !== fields.searchRange
    || view.getUint16(8) !== fields.entrySelector
    || view.getUint16(10) !== fields.rangeShift;
  const badChecksums = kept.filter((entry) => tableChecksum(source, entry) !== entry.checksum);
  const adjustment = head ? view.getUint32(head.offset + CHECKSUM_ADJUSTMENT) : null;
  const adjustmentWrong = head !== undefined && adjustment !== null
    && (sumWithoutAdjustment(source, head) + adjustment) % 0x100000000 !== CHECKSUM_MAGIC;
  // 最后一张表之后、补齐到四字节之外的字节不属于任何表，可以裁掉。
  const contentEnd = kept.reduce((end, entry) => Math.max(end, entry.offset + entry.length), directoryEnd);
  const alignedEnd = Math.ceil(contentEnd / 4) * 4;
  const trailing = bytes.byteLength > alignedEnd ? bytes.byteLength - alignedEnd : 0;

  if (empty.length === 0 && !headerWrong && badChecksums.length === 0 && !adjustmentWrong && trailing === 0) return { kind: "unchanged" };

  const outputLength = Math.max(alignedEnd, 12 + 16 * kept.length);
  const out = new Uint8Array(outputLength);
  out.set(source.subarray(0, Math.min(bytes.byteLength, outputLength)));
  const target = new DataView(out.buffer);

  // 重建目录：按标签顺序登记保留的表，写入按实际数据算出的校验和与正确的头部字段。
  const header = expectedHeader(kept.length);
  target.setUint16(4, kept.length);
  target.setUint16(6, header.searchRange);
  target.setUint16(8, header.entrySelector);
  target.setUint16(10, header.rangeShift);
  kept.forEach((entry, index) => {
    const at = 12 + 16 * index;
    target.setUint32(at, entry.tag);
    target.setUint32(at + 4, tableChecksum(out, entry));
    target.setUint32(at + 8, entry.offset);
    target.setUint32(at + 12, entry.length);
  });
  // 被移掉的目录项留在原位置没有意义；表数据都在原目录之后（上面已校验），这一段不会被引用。
  if (kept.length < count) out.fill(0, 12 + 16 * kept.length, directoryEnd);

  // 目录变了，整份文件的校验和跟着变：重算 head 的 checkSumAdjustment（该字段本身按 0 计入校验和）。
  const headIndex = kept.findIndex((entry) => entry.tag === TAG.head && entry.length >= CHECKSUM_ADJUSTMENT + 4);
  if (headIndex >= 0) {
    const at = kept[headIndex]!.offset + CHECKSUM_ADJUSTMENT;
    target.setUint32(at, 0);
    target.setUint32(at, (CHECKSUM_MAGIC - checksum(out)) >>> 0);
  }

  const repairs = empty.map((entry) => `移除了空的「${tagName(entry.tag)}」表`);
  if (headerWrong) repairs.push("修正了表目录的头部字段");
  if (badChecksums.length > 0 || adjustmentWrong) repairs.push("修正了字体校验和");
  if (trailing > 0) repairs.push(`裁掉了文件末尾多出的 ${trailing} 字节`);
  return { kind: "repaired", bytes: out.buffer, repairs };
}

/**
 * `loca`、`glyf`、`maxp`、`head` 是否自洽。只在这几张表都在且有数据时检查：只带 name 表的测试文件、CFF 字体都不会被误判。
 * 返回问题说明，自洽或无法判断时返回 null。
 */
function glyphsProblem(view: DataView, entries: TableEntry[]): string | null {
  const find = (tag: number) => entries.find((entry) => entry.tag === tag && entry.length > 0);
  const head = find(TAG.head);
  const maxp = find(TAG.maxp);
  const loca = find(TAG.loca);
  const glyf = find(TAG.glyf);
  if (!head || !maxp || !loca || !glyf || maxp.length < 6) return null;
  const format = view.getInt16(head.offset + 50);
  if (format !== 0 && format !== 1) return "head 的字形偏移格式无效";
  const glyphs = view.getUint16(maxp.offset + 4);
  const width = format === 0 ? 2 : 4;
  if (loca.length < (glyphs + 1) * width) return "loca 表长度与字形数量不符";
  // 最后一项是 glyf 表里字形数据的长度，超出就说明 loca 与 glyf 来自不同版本。
  const end = format === 0 ? view.getUint16(loca.offset + glyphs * width) * 2 : view.getUint32(loca.offset + glyphs * width);
  return end > glyf.length ? "loca 与 glyf 的字形数据长度不符" : null;
}

/** 给定表数量时应有的二分查找字段。 */
function expectedHeader(count: number): { searchRange: number; entrySelector: number; rangeShift: number } {
  const widest = 2 ** Math.floor(Math.log2(count));
  return { searchRange: widest * 16, entrySelector: Math.log2(widest), rangeShift: count * 16 - widest * 16 };
}

/** 一张表按 sfnt 约定应有的校验和：`head` 的 checkSumAdjustment 按 0 计入。 */
function tableChecksum(bytes: Uint8Array, entry: TableEntry): number {
  const data = bytes.subarray(entry.offset, entry.offset + entry.length);
  if (entry.tag !== TAG.head || entry.length < CHECKSUM_ADJUSTMENT + 4) return checksum(data);
  const copy = data.slice();
  new DataView(copy.buffer).setUint32(CHECKSUM_ADJUSTMENT, 0);
  return checksum(copy);
}

/** 把 `head` 的 checkSumAdjustment 当作 0 后整份文件的校验和。 */
function sumWithoutAdjustment(bytes: Uint8Array, head: TableEntry): number {
  const copy = bytes.slice();
  new DataView(copy.buffer).setUint32(head.offset + CHECKSUM_ADJUSTMENT, 0);
  return checksum(copy);
}

/** 整份文件的 sfnt 校验和：按大端 32 位字求和，末尾不足四字节按零补齐。 */
function checksum(bytes: Uint8Array): number {
  let sum = 0;
  for (let offset = 0; offset < bytes.length; offset += 4) {
    const word = bytes[offset]! * 0x1000000 + (bytes[offset + 1] ?? 0) * 0x10000 + (bytes[offset + 2] ?? 0) * 0x100 + (bytes[offset + 3] ?? 0);
    sum = (sum + word) % 0x100000000;
  }
  return sum;
}

/** 表标签的四个字符，去掉补位空格。 */
function tagName(tag: number): string {
  return String.fromCharCode(tag >>> 24, (tag >>> 16) & 0xff, (tag >>> 8) & 0xff, tag & 0xff).trimEnd();
}
