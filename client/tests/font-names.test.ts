/** 字体显示名称：从 name 表读出、优先中文名，与服务端 `fonts.read_names` 的规则一致；执行 bun run test，不访问网络。 */
import { expect, test } from "bun:test";
import { fontDisplayName } from "@/features/fonts/names";
import { english, fontBytes } from "./font-files";

/** 读出给定 name 记录的字体的显示名称。 */
const nameOf = (...entries: Parameters<typeof fontBytes>[0]) => fontDisplayName(fontBytes(entries).buffer);

// 场景：字体同时有英文与简体中文名称——显示中文名（得意黑），而不是英文家族名或文件名。
test("中文名优先于英文家族名", () => {
  expect(nameOf(english("Smiley Sans"), [3, 1, 0x0804, 1, "得意黑"])).toBe("得意黑");
  expect(nameOf(english("Zed Display"))).toBe("Zed Display");
});

// 场景：中文名之间简体优先于繁体；只有繁体中文名时用繁体。
test("简体中文名优先于繁体中文名", () => {
  expect(nameOf(english("Font"), [3, 1, 0x0404, 1, "繁體名"], [3, 1, 0x0804, 1, "简体名"])).toBe("简体名");
  expect(nameOf(english("Font"), [3, 1, 0x0c04, 1, "香港名"], [3, 1, 0x0404, 1, "台灣名"])).toBe("台灣名");
});

// 场景：同一语言里字体家族（ID 16）优先于带字重的旧式家族（ID 1）；英文名优先于其他非中文语言。
test("同一语言里字体家族优先，英文优先于其他语言", () => {
  expect(nameOf(english("Alibaba PuHuiTi 2.0 85 Bold"), english("Alibaba PuHuiTi 2.0", 16))).toBe("Alibaba PuHuiTi 2.0");
  expect(nameOf([3, 1, 0x0411, 1, "日本語名"], english("Latin Name"))).toBe("Latin Name");
});

// 场景：Mac 平台的记录（可能是非 Unicode 编码）、空白或不可打印的名称都不作为显示名称。
test("只用 Unicode 编码的可打印名称", () => {
  expect(nameOf([1, 0, 0, 1, "Mac Only"])).toBeNull();
  expect(nameOf([3, 1, 0x0804, 1, "  "], [3, 1, 0x0804, 16, "bad\u0001name"], english("Clean"))).toBe("Clean");
  expect(nameOf(english("x".repeat(101)))).toBeNull();
});

// 场景：不是 sfnt 字体、被截断或没有名称表——返回 null（不画预览条，交给服务端判断），不抛错。
test("读不出名称时返回 null", () => {
  const data = fontBytes([english("Zed Display")]);
  expect(fontDisplayName(new TextEncoder().encode("wOFF0000").buffer)).toBeNull();
  expect(fontDisplayName(data.slice(0, 20).buffer)).toBeNull();
  expect(fontDisplayName(data.slice(0, 40).buffer)).toBeNull();
  expect(fontDisplayName(new ArrayBuffer(0))).toBeNull();
});
