/** 资产组件核心测试：使用真实目录验证所选资产与文字对象传递；执行 bun run test。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { EffectAssets } from "@/features/templates/EffectAssets";
import { defaultEditor, type EffectAsset, type TextRole } from "@/features/templates/model";
import { readCatalog } from "@/features/templates/sdk";

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
