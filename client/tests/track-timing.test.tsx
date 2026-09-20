/** 时间表单行为测试：使用真实组件和状态，验证内存更新、单位换算与对象切换；执行 bun run test。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { TrackTiming } from "@/features/templates/TrackTiming";
import { newDraft } from "@/features/templates/model";
import { setTrackTiming, withTracks } from "@/features/templates/tracks";

/** 使用真实编辑函数保存表单规则，输出模板供断言检查。 */
function TimingEditor() {
  const [draft, setDraft] = useState(() => withTracks(newDraft()));
  const [error, setError] = useState("");
  return <><TrackTiming track={draft.tracks[0]} duration={20} onChange={(timing) => {
    try { setDraft(withTracks(setTrackTiming(draft, "title", timing))); setError(""); }
    catch (reason) { setError((reason as Error).message); }
  }} /><output data-testid="draft">{JSON.stringify(draft.tracks[0])}</output>{error && <p role="alert">{error}</p>}</>;
}

/** 通过组件的键盘与选项交互选择单位，不替换组件内部实现。 */
function selectOption(label: string, name: string) {
  fireEvent.keyDown(screen.getByRole("combobox", { name: label }), { key: "ArrowDown" });
  fireEvent.click(screen.getByRole("option", { name }));
}

// 场景：每次输入立即进入内存草稿；五秒转换为 25%，固定时长与持续到结束均同步预览。
test("时间输入和持续方式自动更新内存草稿", () => {
  render(<TimingEditor />);
  fireEvent.change(screen.getByLabelText("开始时间 / 秒"), { target: { value: "5" } });
  expect(JSON.parse(screen.getByTestId("draft").textContent!).start).toBe(5);
  selectOption("开始方式", "视频时长百分比");
  expect(screen.getByLabelText<HTMLInputElement>("开始位置 / %").value).toBe("25");
  selectOption("持续方式", "固定时长");
  expect(JSON.parse(screen.getByTestId("draft").textContent!)).toMatchObject({ start_mode: "percent", start: 25, duration: 15 });
  expect(screen.getByText("当前预览：第 5～20 秒")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("持续时间 / 秒"), { target: { value: "4.5" } });
  expect(JSON.parse(screen.getByTestId("draft").textContent!).duration).toBe(4.5);
  expect(screen.getByText("当前预览：第 5～9.5 秒")).toBeTruthy();
  selectOption("持续方式", "持续到视频结束");
  expect(JSON.parse(screen.getByTestId("draft").textContent!).duration).toBeNull();
  expect(screen.getByText("当前预览：第 5～20 秒")).toBeTruthy();
  selectOption("开始方式", "指定秒数");
  expect(JSON.parse(screen.getByTestId("draft").textContent!)).toMatchObject({ start_mode: "seconds", start: 5 });
  expect(screen.queryByRole("button")).toBeNull();
});

// 场景：删除输入、输入零时长及 100% 时保留有效规则，修正后立即恢复更新。
test("非法输入保留有效规则并支持继续编辑", () => {
  render(<TimingEditor />);
  selectOption("持续方式", "固定时长");
  for (const value of ["", "0", "-1"]) {
    fireEvent.change(screen.getByLabelText("持续时间 / 秒"), { target: { value } });
    expect(JSON.parse(screen.getByTestId("draft").textContent!).duration).toBe(20);
    expect(screen.getByRole("alert")).toBeTruthy();
  }
  fireEvent.change(screen.getByLabelText("持续时间 / 秒"), { target: { value: "2.5" } });
  expect(JSON.parse(screen.getByTestId("draft").textContent!).duration).toBe(2.5);
  expect(screen.queryByRole("alert")).toBeNull();
  selectOption("开始方式", "视频时长百分比");
  for (const value of ["", "100"]) {
    fireEvent.change(screen.getByLabelText("开始位置 / %"), { target: { value } });
    expect(JSON.parse(screen.getByTestId("draft").textContent!).start).toBe(0);
    expect(screen.getByRole("alert")).toBeTruthy();
  }
  fireEvent.change(screen.getByLabelText("开始位置 / %"), { target: { value: "50" } });
  expect(JSON.parse(screen.getByTestId("draft").textContent!).start).toBe(50);
  expect(screen.queryByRole("alert")).toBeNull();
});

// 场景：开始位置超出当前视频时，固定时长等待用户输入有效值，填写后保存规则以供更长视频使用。
test("视频结束后没有可自动填入的固定时长", () => {
  render(<TimingEditor />);
  fireEvent.change(screen.getByLabelText("开始时间 / 秒"), { target: { value: "25" } });
  selectOption("持续方式", "固定时长");
  expect(screen.getByLabelText<HTMLInputElement>("持续时间 / 秒").value).toBe("");
  expect(screen.getByRole("alert")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("持续时间 / 秒"), { target: { value: "2" } });
  expect(JSON.parse(screen.getByTestId("draft").textContent!)).toMatchObject({ start: 25, duration: 2 });
  expect(screen.queryByRole("alert")).toBeNull();
});

// 场景：切换对象显示其独立规则；视频时长变化只更新计算结果，不再次修改规则。
test("切换对象和预览时长保留各自时间规则", () => {
  const draft = withTracks(newDraft());
  const selected = { ...draft.tracks[1], start_mode: "percent" as const, start: 50, duration: null };
  const changes: unknown[] = [];
  const view = render(<TrackTiming track={draft.tracks[0]} duration={20} onChange={(value) => changes.push(value)} />);
  fireEvent.change(screen.getByLabelText("开始时间 / 秒"), { target: { value: "7" } });
  view.rerender(<TrackTiming track={selected} duration={20} onChange={(value) => changes.push(value)} />);
  expect(screen.getByLabelText<HTMLInputElement>("开始位置 / %").value).toBe("50");
  expect(screen.getByRole("status").textContent).toContain("第 10～20 秒");
  view.rerender(<TrackTiming track={selected} duration={60} onChange={(value) => changes.push(value)} />);
  expect(screen.getByRole("status").textContent).toContain("第 30～60 秒");
  expect(changes).toEqual([{ start_mode: "seconds", start: 7, duration: null }]);
});
