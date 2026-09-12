/** 时钟回归测试：本地日期格式、跨日更新和卸载清理；在 client 下执行 bun run test。 */
import { expect, setSystemTime, spyOn, test } from "bun:test";
import { render, screen, waitFor } from "@testing-library/react";
import CurrentTime from "@/components/CurrentTime";

// 固定本地跨年边界，验证真实定时器刷新日期与时间，并在卸载时停止计时。
test("时钟显示本地时间、每秒跨年更新并清理定时器", async () => {
  const before = new Date(2026, 11, 31, 23, 59, 59);
  const after = new Date(2027, 0, 1, 0, 0, 0);
  const interval = spyOn(window, "setInterval");
  const clear = spyOn(window, "clearInterval");
  setSystemTime(before);
  const view = render(<CurrentTime />);
  try {
    const time = screen.getByRole("time");
    expect(time.textContent).toBe("2026/12/31 23:59:59");
    expect(time.getAttribute("datetime")).toBe(before.toISOString());
    expect(interval.mock.calls[0][1]).toBe(1000);
    setSystemTime(after);
    await waitFor(() => {
      expect(time.textContent).toBe("2027/01/01 00:00:00");
      expect(time.getAttribute("datetime")).toBe(after.toISOString());
    }, { timeout: 2000 });
    view.unmount();
    expect(clear).toHaveBeenCalledWith(interval.mock.results[0].value);
  } finally {
    view.unmount();
    setSystemTime();
  }
});
