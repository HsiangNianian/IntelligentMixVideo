/** 使用真实控件与目录验证添加、选择和统一开关；执行 bun run test。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { TransitionInspector } from "@/features/templates/TemplateInspector";
import { newDraft } from "@/features/templates/model";
import { readCatalog } from "@/features/templates/sdk";
import { addTrack } from "@/features/templates/tracks";
import { compactTransitions, setTransitionDuration } from "@/features/templates/transitions";

test("右侧添加列表在关闭和重新开启转场后完整保留", () => {
  const catalog = readCatalog();
  const asset = catalog.find((item) => item.category === "transition/normal")!;
  function Editor() {
    const [draft, setDraft] = useState(newDraft);
    return <TransitionInspector draft={draft} catalog={catalog} sprites={[]}
      onChoose={(_id, value) => setDraft(addTrack(draft, catalog.find((item) => item.id === value)!, "transition").draft)}
      onRemove={(id) => setDraft(compactTransitions({ ...draft, tracks: draft.tracks.filter((track) => track.id !== id) }))}
      onDuration={(value) => setDraft(setTransitionDuration(draft, value))}
      onMode={(value) => setDraft({ ...draft, transition_mode: value })}
      onEnabled={(value) => setDraft({ ...draft, transition_enabled: value })} onSelect={() => {}} />;
  }
  render(<Editor />);
  expect(screen.getByRole("radio", { name: "顺序选择" }).getAttribute("aria-checked")).toBe("true");
  for (let index = 0; index < 3; index += 1) {
    fireEvent.click(screen.getByRole("combobox", { name: "添加转场" }));
    fireEvent.click(screen.getByRole("option", { name: asset.name }));
    expect(screen.getAllByRole("combobox", { name: /转场 \d 效果/ })).toHaveLength(index + 1);
  }
  fireEvent.click(screen.getByRole("radio", { name: "随机选择" }));
  fireEvent.change(screen.getByLabelText("转场持续时间 / 秒"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("switch", { name: "使用转场" }));
  expect(screen.getByRole("switch", { name: "使用转场" }).getAttribute("aria-checked")).toBe("false");
  expect(screen.getAllByRole("combobox", { name: /转场 \d 效果/ })).toHaveLength(3);
  fireEvent.click(screen.getByRole("switch", { name: "使用转场" }));
  expect(screen.getByRole("switch", { name: "使用转场" }).getAttribute("aria-checked")).toBe("true");
  expect(screen.getByRole("radio", { name: "随机选择" }).getAttribute("aria-checked")).toBe("true");
  expect(screen.getByLabelText<HTMLInputElement>("转场持续时间 / 秒").value).toBe("2");
});
