/** 在主页搜索结果中按 ID 打开补图目标；同名或相似名称可能跨越多页。 */
export async function openBackfillTemplate(page, entry) {
  await page.getByRole("searchbox", { name: "搜索模板名称" }).fill(entry.name);
  const results = page.getByRole("region", { name: "全部模板", exact: true });
  await results.getByRole("heading", { name: "搜索结果", exact: true }).waitFor();
  const target = results.locator(`button[data-template-id="${entry.templateId}"]`);
  const next = results.getByRole("button", { name: "下一页" });
  while (!await target.count()) {
    if (!await next.count() || await next.isDisabled()) throw new Error(`搜索结果中找不到模板 ${entry.name}（${entry.templateId}）`);
    const previous = results.getByText(await results.getByRole("status").innerText(), { exact: true });
    await next.click();
    await previous.waitFor({ state: "hidden" });
  }
  await target.click();
}
