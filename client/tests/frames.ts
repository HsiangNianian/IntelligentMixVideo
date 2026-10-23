/** 测试共用的逐帧回调替身：接管 requestAnimationFrame，由用例逐次执行；用例结束时随 mock.restore 恢复。 */
import { spyOn } from "bun:test";
import { act } from "@testing-library/react";

/** 接管逐帧回调，由用例逐次执行，返回执行下一帧的函数与尚未执行的帧数。 */
export function frames() {
  const pending = new Map<number, FrameRequestCallback>();
  let next = 0;
  spyOn(window, "requestAnimationFrame").mockImplementation((callback) => { pending.set(++next, callback); return next; });
  spyOn(window, "cancelAnimationFrame").mockImplementation((id) => { pending.delete(id); });
  return {
    step: () => act(async () => {
      const due = [...pending.values()];
      pending.clear();
      due.forEach((callback) => callback(0));
    }),
    count: () => pending.size,
  };
}
