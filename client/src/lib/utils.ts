/** 样式工具先组合条件类名，再合并冲突的 Tailwind 工具类，让调用方样式生效。 */
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** 合并可选 className，并按 Tailwind 规则解决覆盖冲突。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
