/** 仅保存已落盘内容的位置，避免恢复草稿、播放和撤销历史；完整重启跨 origin 也由原生存储恢复。 */
import type { TemplateSelection } from '@/features/templates/TemplateHome';
export type Recovery = { workspace?: string; template?: TemplateSelection | null; remotionWork?: string | null };
export const recovery: Recovery = {};
export let restored: Recovery = {};
export function restoreRecovery(value: Recovery | null) {
  restored = value && typeof value === 'object' ? value : {};
  for (const key of Object.keys(recovery)) delete recovery[key as keyof Recovery];
  Object.assign(recovery, restored);
}
