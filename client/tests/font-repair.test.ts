/** 上传前的字体目录级修复：零长度表被移除而其余表数据不变、修复幂等、结构损坏时给出原因；执行 bun run test，不访问网络。 */
import { expect, test } from "bun:test";
import { repairFont } from "@/features/fonts/repair";
import { english, fontBytes, type ExtraTable } from "./font-files";

/** 表目录：标签、偏移与长度。 */
function directory(bytes: ArrayBuffer): { tag: string; offset: number; length: number }[] {
  const view = new DataView(bytes);
  return Array.from({ length: view.getUint16(4) }, (_, index) => {
    const at = 12 + 16 * index;
    const tag = String.fromCharCode(view.getUint8(at), view.getUint8(at + 1), view.getUint8(at + 2), view.getUint8(at + 3));
    return { tag: tag.trimEnd(), offset: view.getUint32(at + 8), length: view.getUint32(at + 12) };
  });
}

/** 整份文件的 sfnt 校验和，与修复时的算法一致（末尾不足四字节按零补齐）。 */
function checksum(bytes: Uint8Array): number {
  let sum = 0;
  for (let offset = 0; offset < bytes.length; offset += 4) {
    sum = (sum + bytes[offset]! * 0x1000000 + (bytes[offset + 1] ?? 0) * 0x10000 + (bytes[offset + 2] ?? 0) * 0x100 + (bytes[offset + 3] ?? 0)) % 0x100000000;
  }
  return sum;
}

/** 这份文件里 checkSumAdjustment 应为的值：该字段按 0 计入校验和，两者相加得常数。 */
function expectedAdjustment(bytes: ArrayBuffer): number {
  const copy = new Uint8Array(bytes.slice(0));
  const head = directory(bytes).find((entry) => entry.tag === "head")!;
  new DataView(copy.buffer).setUint32(head.offset + 8, 0);
  return (0xb1b0afba - checksum(copy)) >>> 0;
}

/** 重新算出与当前内容一致的 checkSumAdjustment：单独验证某一项检查时，不让整份文件的校验和先失败。 */
function resign(bytes: Uint8Array<ArrayBuffer>): Uint8Array<ArrayBuffer> {
  const head = directory(bytes.buffer).find((entry) => entry.tag === "head");
  if (head) new DataView(bytes.buffer).setUint32(head.offset + 8, expectedAdjustment(bytes.buffer));
  return bytes;
}

/** 缓冲区里某张表的数据。 */
function tableOf(bytes: ArrayBuffer, tag: string): Uint8Array {
  const entry = directory(bytes).find((item) => item.tag === tag)!;
  return new Uint8Array(bytes.slice(entry.offset, entry.offset + entry.length));
}

/** head 表：54 字节，字形偏移格式可指定。 */
function head(format = 0): ExtraTable {
  const data = new Uint8Array(54);
  const view = new DataView(data.buffer);
  view.setUint32(0, 0x00010000);
  view.setUint32(12, 0x5f0f3cf5);
  view.setUint16(18, 2048);
  view.setInt16(50, format);
  return ["head", data];
}

/** maxp 表：版本 1.0 与字形数量。 */
function maxp(glyphs = 1): ExtraTable {
  const data = new Uint8Array(32);
  const view = new DataView(data.buffer);
  view.setUint32(0, 0x00010000);
  view.setUint16(4, glyphs);
  return ["maxp", data];
}

/** glyf 表：一个没有轮廓的字形。 */
function glyf(length = 12): ExtraTable {
  return ["glyf", new Uint8Array(length)];
}

/** loca 表：短格式，最后一项是 glyf 的长度（按半字存）。 */
function loca(end = 12): ExtraTable {
  const data = new Uint8Array(4);
  new DataView(data.buffer).setUint16(2, end / 2);
  return ["loca", data];
}

/** 短格式 loca：按给定的字形偏移（字节）写入每一项。 */
function locaOffsets(...offsets: number[]): ExtraTable {
  const data = new Uint8Array(offsets.length * 2);
  offsets.forEach((offset, index) => new DataView(data.buffer).setUint16(index * 2, offset / 2));
  return ["loca", data];
}

/** 一张零长度表。 */
function empty(tag: string): ExtraTable {
  return [tag, new Uint8Array(0)];
}

/** 一份结构自洽、带字形表的字体字节。 */
const soundFont = (...extras: ExtraTable[]) => fontBytes([english("Zed Display")], [head(), maxp(), glyf(), loca(), ...extras]);

/** OpenType/CFF 字节：只有 name 与给定额外表，版本改成 OTTO；没有 head 时不需要重算校验和。 */
function cffFont(...extras: ExtraTable[]): Uint8Array<ArrayBuffer> {
  const bytes = fontBytes([english("Zed Display")], extras);
  new DataView(bytes.buffer).setUint32(0, 0x4f54544f);
  return bytes;
}

// 场景：做字工具导出的字体带着长度为 0 的 cvt、DSIG 表——删掉它们、重建目录与头部字段、重算 head 校验和，其余表一个字节不变。
test("移除零长度的表并重建目录", () => {
  const original = soundFont(empty("cvt "), empty("DSIG"));
  const result = repairFont(original.buffer);
  expect(result.kind).toBe("repaired");
  if (result.kind !== "repaired") return;
  expect(directory(result.bytes).map((entry) => entry.tag)).toEqual(["glyf", "head", "loca", "maxp", "name"]);
  const view = new DataView(result.bytes);
  expect([view.getUint16(4), view.getUint16(6), view.getUint16(8), view.getUint16(10)]).toEqual([5, 64, 2, 16]);
  expect(result.repairs).toEqual(["移除了空的「DSIG」表", "移除了空的「cvt」表"]);
  // 保留的表的偏移与数据都不动；head 只有 checkSumAdjustment（表内 8～12 字节）按新目录重算。
  for (const tag of ["glyf", "loca", "maxp", "name"]) {
    expect(tableOf(result.bytes, tag)).toEqual(tableOf(original.buffer, tag));
  }
  const headBefore = tableOf(original.buffer, "head");
  const headAfter = tableOf(result.bytes, "head");
  expect(headAfter.slice(0, 8)).toEqual(headBefore.slice(0, 8));
  expect(headAfter.slice(12)).toEqual(headBefore.slice(12));
  // 整份文件的校验和回到常数。
  expect(new DataView(result.bytes).getUint32(directory(result.bytes).find((entry) => entry.tag === "head")!.offset + 8)).toBe(expectedAdjustment(result.bytes));
});

// 场景：修复结果再次上传（或别人已经传过同样修好的文件）——第二次修复不产生任何改动，按内容去重不会漂移。
test("修复是幂等的", () => {
  const first = repairFont(soundFont(empty("cvt ")).buffer);
  expect(first.kind).toBe("repaired");
  if (first.kind !== "repaired") return;
  expect(repairFont(first.bytes)).toEqual({ kind: "unchanged" });
  expect(repairFont(first.bytes)).not.toHaveProperty("bytes");
});

// 场景：字体本身没有零长度表——即使头部字段、表校验和或尾部字节与规范不符，也不重写；摘要与预览继续用原字节。
test("没有零长度表时不做任何改动", () => {
  expect(repairFont(soundFont().buffer)).toEqual({ kind: "unchanged" });
  const bytes = soundFont();
  new DataView(bytes.buffer).setUint16(6, 0);
  new DataView(bytes.buffer).setUint16(10, 0);
  const index = directory(bytes.buffer).findIndex((entry) => entry.tag === "name");
  new DataView(bytes.buffer).setUint32(12 + 16 * index + 4, 1);
  const extended = new Uint8Array(bytes.length + 4);
  extended.set(bytes);
  extended.set([1, 2, 3, 4], bytes.length);
  expect(repairFont(extended.buffer)).toEqual({ kind: "unchanged" });
});

// 场景：不是认得的 sfnt（任意字节、字体集合）——不猜，交给浏览器的解码检查。
test("不认识的格式原样返回", () => {
  expect(repairFont(new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]).buffer)).toEqual({ kind: "unchanged" });
});

// 场景：必需表被写成零长度（内容已丢）——不能靠删表修好，说明是哪张表。
test("必需表为空时说明原因", () => {
  const result = repairFont(soundFont(empty("loca")).buffer);
  expect(result).toEqual({ kind: "damaged", detail: "「loca」表是空的" });
});

// 场景：OTS 同样要求 OS/2 与 post——空的这两张表不能当可选表删掉，否则浏览器仍解不出来。
test("OTS 要求的 OS/2 与 post 为空时说明原因", () => {
  expect(repairFont(soundFont(empty("OS/2")).buffer)).toEqual({ kind: "damaged", detail: "「OS/2」表是空的" });
  expect(repairFont(soundFont(empty("post")).buffer)).toEqual({ kind: "damaged", detail: "「post」表是空的" });
});

// 场景：TrueType 字体里遗留的空的 CFF 表与当前轮廓格式无关——删掉它，而不是拒绝整个字体。
test("移除不用的空轮廓表", () => {
  const result = repairFont(soundFont(empty("CFF ")).buffer);
  expect(result.kind).toBe("repaired");
  if (result.kind !== "repaired") return;
  expect(result.repairs).toEqual(["移除了空的「CFF」表"]);
  expect(directory(result.bytes).map((entry) => entry.tag)).toEqual(["glyf", "head", "loca", "maxp", "name"]);
});

// 场景：CFF 字体里遗留的空的 glyf/loca——按 CFF 轮廓处理，删掉不用的空表。
test("CFF 字体移除不用的空 glyf 与 loca", () => {
  const bytes = cffFont(["CFF ", new Uint8Array([1, 2, 3, 4])], empty("glyf"), empty("loca"));
  const result = repairFont(bytes.buffer);
  expect(result.kind).toBe("repaired");
  if (result.kind !== "repaired") return;
  expect(result.repairs).toEqual(["移除了空的「glyf」表", "移除了空的「loca」表"]);
  expect(directory(result.bytes).map((entry) => entry.tag)).toEqual(["CFF", "name"]);
});

// 场景：CFF 字体只有空的 CFF 而没有 CFF2——没有可用轮廓，拒绝而不是生成空字体。
test("没有可用的轮廓表时说明原因", () => {
  expect(repairFont(cffFont(empty("CFF ")).buffer)).toEqual({ kind: "damaged", detail: "字体没有可用的轮廓表" });
});

// 场景：某张表的偏移落进表目录（表与目录重叠）——数据位置不可信，不尝试修复。
test("表与表目录重叠时说明原因", () => {
  const bytes = soundFont();
  const index = directory(bytes.buffer).findIndex((entry) => entry.tag === "name");
  new DataView(bytes.buffer).setUint32(12 + 16 * index + 8, 12);
  expect(repairFont(bytes.buffer)).toEqual({ kind: "damaged", detail: "「name」表与表目录重叠" });
});

// 场景：两个非空表的范围重叠——重写 head 校验和可能改到另一张表，拒绝而不是修坏。
test("非空表范围重叠时说明原因", () => {
  const bytes = soundFont(empty("cvt "));
  const entries = directory(bytes.buffer);
  const glyf = entries.find((entry) => entry.tag === "glyf")!;
  const nameIndex = entries.findIndex((entry) => entry.tag === "name");
  new DataView(bytes.buffer).setUint32(12 + 16 * nameIndex + 8, glyf.offset + 1);
  expect(repairFont(bytes.buffer)).toEqual({ kind: "damaged", detail: "「name」表与「glyf」表重叠" });
});

// 场景：目录里某张表的长度超出文件末尾——数据根本不在文件里，不能裁成一份看似合法的字体。
test("表超出文件末尾时说明原因", () => {
  const bytes = soundFont(empty("cvt "));
  const index = directory(bytes.buffer).findIndex((entry) => entry.tag === "name");
  new DataView(bytes.buffer).setUint32(12 + 16 * index + 12, bytes.length);
  expect(repairFont(bytes.buffer)).toEqual({ kind: "damaged", detail: "「name」表超出文件末尾" });
});

// 场景：表目录本身越界（文件被截断或头部损坏）——说明原因，不尝试修复。
test("表目录超出文件末尾时说明原因", () => {
  const bytes = soundFont();
  new DataView(bytes.buffer).setUint16(4, 1000);
  expect(repairFont(bytes.buffer)).toEqual({ kind: "damaged", detail: "表目录超出文件末尾" });
});

// 场景：loca 的最后一项比 glyf 还长——两张表来自不同版本，字形数据对不上。
test("loca 与 glyf 不符时说明原因", () => {
  expect(repairFont(fontBytes([english("Zed Display")], [head(), maxp(), glyf(), loca(16)]).buffer)).toEqual({
    kind: "damaged", detail: "loca 与 glyf 的字形数据长度不符",
  });
});

// 场景：loca 的第一项不是 0——字形数据往前越界，说明原因。
test("loca 的第一个偏移不是 0 时说明原因", () => {
  expect(repairFont(fontBytes([english("Zed Display")], [head(), maxp(), glyf(), locaOffsets(2, 12)]).buffer)).toEqual({
    kind: "damaged", detail: "loca 的第一个偏移不是 0",
  });
});

// 场景：loca 的中间偏移下降——字形区间错乱，说明原因。
test("loca 的偏移不是递增时说明原因", () => {
  expect(repairFont(fontBytes([english("Zed Display")], [head(), maxp(2), glyf(), locaOffsets(0, 8, 4)]).buffer)).toEqual({
    kind: "damaged", detail: "loca 的偏移不是递增的",
  });
});

// 场景：目录里只有一张零长度的可选表（没有任何可用的表）——不生成没有表的字体，说明原因。
test("没有可用的表时说明原因", () => {
  const bytes = new Uint8Array(12 + 16);
  const view = new DataView(bytes.buffer);
  view.setUint32(0, 0x00010000);
  view.setUint16(4, 1);
  view.setUint32(12, 0x67617370);
  expect(repairFont(bytes.buffer)).toEqual({ kind: "damaged", detail: "字体没有可用的表" });
});

// 场景：head 太短或字形偏移格式无效——浏览器同样会拒绝，先给出具体原因。
test("head 不完整或格式无效时说明原因", () => {
  expect(repairFont(fontBytes([english("Zed Display")], [["head", new Uint8Array(20)]]).buffer)).toEqual({
    kind: "damaged", detail: "head 表不完整",
  });
  expect(repairFont(fontBytes([english("Zed Display")], [head(2), maxp(), glyf(), loca()]).buffer)).toEqual({
    kind: "damaged", detail: "head 的字形偏移格式无效",
  });
});

// 场景：目录的二分查找字段与表数量不符（重排目录的工具常见）——在因零长度表重写时写出正确字段，表数据不动。
test("修正目录的二分查找字段", () => {
  const bytes = soundFont(empty("cvt "));
  new DataView(bytes.buffer).setUint16(6, 0);
  new DataView(bytes.buffer).setUint16(10, 0);
  resign(bytes);
  const result = repairFont(bytes.buffer);
  expect(result.kind).toBe("repaired");
  if (result.kind !== "repaired") return;
  expect(result.repairs).toEqual(["移除了空的「cvt」表", "修正了表目录的头部字段"]);
  const view = new DataView(result.bytes);
  expect([view.getUint16(4), view.getUint16(6), view.getUint16(8), view.getUint16(10)]).toEqual([5, 64, 2, 16]);
  expect(tableOf(result.bytes, "name")).toEqual(tableOf(bytes.buffer, "name"));
});

// 场景：目录里某张表的校验和与实际数据不符——在因零长度表重写时按数据重算。
test("重算与实际数据不符的表校验和", () => {
  const bytes = soundFont(empty("cvt "));
  const index = directory(bytes.buffer).findIndex((entry) => entry.tag === "name");
  new DataView(bytes.buffer).setUint32(12 + 16 * index + 4, 1);
  resign(bytes);
  const result = repairFont(bytes.buffer);
  expect(result.kind).toBe("repaired");
  if (result.kind !== "repaired") return;
  expect(result.repairs).toEqual(["移除了空的「cvt」表", "修正了字体校验和"]);
  const nameIndex = directory(result.bytes).findIndex((entry) => entry.tag === "name");
  expect(new DataView(result.bytes).getUint32(12 + 16 * nameIndex + 4)).toBe(checksum(tableOf(result.bytes, "name")));
});

// 场景：head 的 checkSumAdjustment 与文件内容不符——在因零长度表重写时重算，让整份文件的校验和回到常数。
test("重算 head 的校验和调整值", () => {
  const bytes = soundFont(empty("cvt "));
  const head = directory(bytes.buffer).find((entry) => entry.tag === "head")!;
  new DataView(bytes.buffer).setUint32(head.offset + 8, 0x12345678);
  const result = repairFont(bytes.buffer);
  expect(result.kind).toBe("repaired");
  if (result.kind !== "repaired") return;
  expect(result.repairs).toEqual(["移除了空的「cvt」表", "修正了字体校验和"]);
  const repairedHead = directory(result.bytes).find((entry) => entry.tag === "head")!;
  expect(new DataView(result.bytes).getUint32(repairedHead.offset + 8)).toBe(expectedAdjustment(result.bytes));
});

// 场景：文件末尾多出不属于任何表的数据——在因零长度表重写时裁掉，重新校正 head 的调整值。
test("裁掉文件末尾多出的字节", () => {
  const bytes = soundFont(empty("cvt "));
  const extended = new Uint8Array(bytes.length + 4);
  extended.set(bytes);
  extended.set([1, 2, 3, 4], bytes.length);
  resign(extended);
  const result = repairFont(extended.buffer);
  expect(result.kind).toBe("repaired");
  if (result.kind !== "repaired") return;
  expect(result.repairs).toEqual(["移除了空的「cvt」表", "裁掉了文件末尾多出的 4 字节"]);
  const withoutTrailing = repairFont(bytes.buffer);
  expect(withoutTrailing.kind).toBe("repaired");
  if (withoutTrailing.kind !== "repaired") return;
  expect(new Uint8Array(result.bytes)).toEqual(new Uint8Array(withoutTrailing.bytes));
});
