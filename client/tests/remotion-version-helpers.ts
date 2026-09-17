/** 版本卡片测试操作：定位最新结果，显式展开默认折叠的代码；不依赖全局代码面板。 */
import { fireEvent, screen, within } from "@testing-library/react";

/** 使用用户可见的“最新”标记定位卡片，历史版本不影响旧功能用例。 */
function latestCard(): HTMLElement {
  const marker = screen.getByText("最新", { selector: "span" });
  const card = marker.closest<HTMLElement>('[aria-label^="成功版本 V"]');
  if (!card) throw new Error("没有最新成功版本卡片");
  return card;
}

/** 返回最新结果的复制入口；尚无结果的用例应断言不存在任何复制按钮。 */
export function latestCopy() {
  return within(latestCard()).getByRole<HTMLButtonElement>("button", {
    name: "复制代码",
  });
}

/** 展开最新卡片后读取对应导出，模拟用户查看代码。 */
export function latestCode() {
  const card = latestCard();
  const trigger = within(card).getByRole("button", { name: /展开 V\d+ 代码/ });
  if (trigger.getAttribute("aria-expanded") !== "true")
    fireEvent.click(trigger);
  return within(card).getByLabelText("模板 TSX 代码");
}
