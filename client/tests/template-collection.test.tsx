/** 模板摘要展示与删除确认测试；使用真实组件，通过取消结束操作，不请求存储。 */
import { expect, test } from "bun:test";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { TemplateCollection, type TemplateSelection } from "@/features/templates/TemplateHome";
import { savedTemplate, templateSummary } from "./fixtures";

// 场景：无效模板只可进入删除确认，取消后仍可选择同库有效模板。
test("无效记录显示原因且不能打开，删除确认保留有效模板", () => {
  const valid = templateSummary(savedTemplate());
  const invalid = { ...valid, template_id: "invalid-template", name: "无效模板", validation_error: "转场必须指定视频交界处", track_count: undefined };
  const selections: TemplateSelection[] = [];
  render(<TemplateCollection environment="cloud"
    collection={{ templates: [valid, invalid], loading: false, error: "", unavailable: false, refresh: () => {} }}
    onSelect={(selection) => selections.push(selection)} />);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "选择模板：无效模板" }).disabled).toBe(true);
  expect(screen.getByText(invalid.validation_error)).toBeTruthy();
  expect(screen.getByText(`ID：${invalid.template_id}`)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "删除无效模板" }));
  const dialog = screen.getByRole("dialog");
  expect(dialog.textContent).toContain(invalid.template_id);
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  fireEvent.click(screen.getByRole("button", { name: `选择模板：${valid.name}` }));
  expect(selections).toEqual([{ environment: "cloud", templateId: valid.template_id }]);
});
