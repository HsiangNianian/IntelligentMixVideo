/** 资产组件核心测试：使用真实目录验证所选资产与文字对象传递；执行 bun run test。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { AppliedEffects, EffectAssets } from "@/features/templates/EffectAssets";
import { defaultEditor, newDraft, type EffectAsset, type TextRole } from "@/features/templates/model";
import { readCatalog } from "@/features/templates/sdk";
import { addTrack } from "@/features/templates/tracks";

// 场景：转场存储顺序与编号不同，画面对象按编号展示，点击后仍选择对应对象。
test("画面对象按转场编号升序展示", () => {
  const catalog = readCatalog();
  const asset = catalog.find((item) => item.category === "transition/normal")!;
  let draft = newDraft();
  for (const boundary of [2, 1, 4, 3, 5]) draft = addTrack(draft, asset, "transition", undefined, boundary).draft;
  const originalIds = draft.tracks.map((track) => track.id);
  const selections: string[] = [];
  render(<AppliedEffects draft={draft} catalog={catalog} selected={null} onSelect={(id) => selections.push(id)} />);
  const buttons = screen.getAllByRole("button");
  expect(buttons.map((button) => button.getAttribute("aria-label"))).toEqual([
    "编辑顶部标题", "编辑底部字幕", "编辑镜头转场 1", "编辑镜头转场 2", "编辑镜头转场 3", "编辑镜头转场 4", "编辑镜头转场 5",
  ]);
  fireEvent.click(buttons[2]);
  expect(selections).toEqual([draft.tracks.find((track) => track.transition_boundary === 1)!.id]);
  expect(draft.tracks.map((track) => track.id)).toEqual(originalIds);
});

// 场景：标题和字幕资产搜索均不提供停用花字，其他花字仍可选择。
test.each(["title", "subtitle"] as const)("%s 资产列表排除停用花字", (target) => {
  const catalog = readCatalog();
  expect(catalog.some((asset) => asset.id === "flower/CS0003-000002")).toBe(true);
  render(<EffectAssets catalog={catalog} textTarget={target}
    onTextTarget={() => {}} onAsset={() => {}} />);
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索特效" }), { target: { value: "CS0003-000002" } });
  expect(screen.queryByRole("button", { name: /应用花字/ })).toBeNull();
  expect(screen.getByText("没有匹配的特效")).toBeTruthy();
  fireEvent.change(screen.getByRole("searchbox", { name: "搜索特效" }), { target: { value: "CS0003-000001" } });
  expect(screen.getByRole("button", { name: /应用花字/ })).toBeTruthy();
});

// 场景：选择字幕后点击真实目录资产，组件输出对应资产和字幕目标。
test("资产组件传递所选资产与文字对象", async () => {
  const catalog = readCatalog();
  const selections: { asset: EffectAsset; role: TextRole }[] = [];
  /** 使用受控目标接收选择结果，资产应用规则由纯函数测试覆盖。 */
  function Assets() {
    const [target, setTarget] = useState<TextRole>("title");
    return <EffectAssets editor={defaultEditor} catalog={catalog} textTarget={target}
      onTextTarget={setTarget} onAsset={(asset, role) => selections.push({ asset, role })} />;
  }
  render(<Assets />);
  fireEvent.keyDown(screen.getByRole("combobox", { name: "应用到" }), { key: "ArrowDown" });
  fireEvent.keyDown(await screen.findByRole("option", { name: "底部字幕" }), { key: "Enter" });
  const asset = catalog.find((item) => item.category === "flower")!;
  fireEvent.click(screen.getByRole("button", { name: `应用花字：${asset.name}` }));
  expect(selections).toEqual([{ asset, role: "subtitle" }]);
});
