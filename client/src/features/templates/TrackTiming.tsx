/** 对象时间规则表单；输入变化立即更新内存草稿，展示当前视频中的计算结果。 */
import { useEffect, useId, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { EffectTrack } from "./model";
import { resolveTrack } from "./tracks";

/** 切换开始方式按当前视频换算；非法预览区间显示说明，仍允许编辑模板规则。 */
export function TrackTiming({ track, duration, onChange }: {
  track: EffectTrack;
  duration: number;
  onChange: (timing: Pick<EffectTrack, "start_mode" | "start" | "duration">) => void;
}) {
  const id = useId();
  const [mode, setMode] = useState(track.start_mode);
  const [start, setStart] = useState(String(track.start));
  const [length, setLength] = useState(track.duration === null ? "" : String(track.duration));
  const [untilEnd, setUntilEnd] = useState(track.duration === null);
  useEffect(() => {
    setMode(track.start_mode); setStart(String(track.start));
    setLength(track.duration === null ? "" : String(track.duration)); setUntilEnd(track.duration === null);
  }, [track.id, track.start_mode, track.start, track.duration]);
  let result: string;
  try {
    const resolved = resolveTrack(track, duration);
    result = resolved.end > resolved.start ? `当前预览：第 ${Number(resolved.start.toFixed(3))}～${Number(resolved.end.toFixed(3))} 秒${resolved.notice ? `；${resolved.notice}` : ""}` : resolved.notice;
  } catch (error) { result = error instanceof Error ? error.message : "时间规则无法应用到当前视频"; }

  /** 将当前输入交给工作区校验并更新内存；空输入保留为无效数值。 */
  function updateTiming(nextMode = mode, nextStart = start, nextLength = length, nextUntilEnd = untilEnd) {
    onChange({ start_mode: nextMode, start: nextStart.trim() ? Number(nextStart) : NaN, duration: nextUntilEnd ? null : nextLength.trim() ? Number(nextLength) : NaN });
  }

  /** 在有有效预览时长时换算输入值，并立即更新内存中的开始规则。 */
  function changeMode(value: string) {
    if (value !== "seconds" && value !== "percent") throw new Error("开始方式无效");
    const nextStart = value !== mode && start.trim() && duration > 0 ? String(Number((value === "percent" ? Number(start) / duration * 100 : Number(start) * duration / 100).toFixed(6))) : start;
    setStart(nextStart);
    setMode(value);
    updateTiming(value, nextStart);
  }

  /** 持续到结束改为固定时长时，使用当前视频中剩余的区间；没有有效区间则等待输入。 */
  function changeDurationMode(value: string) {
    const nextUntilEnd = value === "end";
    const remaining = duration - (mode === "percent" ? Number(start) * duration / 100 : Number(start));
    const nextLength = !nextUntilEnd && !length && start.trim() && Number.isFinite(remaining) && remaining > 0
      ? String(Number(remaining.toFixed(6))) : length;
    setUntilEnd(nextUntilEnd);
    setLength(nextLength);
    updateTiming(mode, start, nextLength, nextUntilEnd);
  }

  return <div className="space-y-3 border-b p-4" aria-label="轨道时间设置" onKeyDown={(event) => { if (event.key === "Enter" && event.target instanceof HTMLInputElement) event.preventDefault(); }}>
    <div className="space-y-2"><Label htmlFor={`${id}-mode`}>开始方式</Label>
      <Select value={mode} onValueChange={changeMode}><SelectTrigger id={`${id}-mode`}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="seconds">指定秒数</SelectItem><SelectItem value="percent">视频时长百分比</SelectItem></SelectContent></Select>
    </div>
    <div className="space-y-2"><Label htmlFor={`${id}-start`}>开始{mode === "percent" ? "位置 / %" : "时间 / 秒"}</Label><Input id={`${id}-start`} type="number" required min={track.target === "transition" ? Number.MIN_VALUE : 0} max={mode === "percent" ? 99.999999 : undefined} step="any" value={start} onChange={(event) => { setStart(event.target.value); updateTiming(mode, event.target.value); }} /></div>
    <div className="space-y-2"><Label htmlFor={`${id}-duration-mode`}>持续方式</Label>
      <Select value={untilEnd ? "end" : "seconds"} onValueChange={changeDurationMode}><SelectTrigger id={`${id}-duration-mode`}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="seconds">固定时长</SelectItem>{track.target !== "transition" && <SelectItem value="end">持续到视频结束</SelectItem>}</SelectContent></Select>
    </div>
    {!untilEnd && <div className="space-y-2"><Label htmlFor={`${id}-length`}>持续时间 / 秒</Label><Input id={`${id}-length`} type="number" required min={track.target === "transition" ? 0.1 : Number.MIN_VALUE} max={track.target === "transition" ? 3 : undefined} step="any" value={length} onChange={(event) => { setLength(event.target.value); updateTiming(mode, start, event.target.value); }} /></div>}
    <p className="text-xs text-muted-foreground" role="status">{result}</p>
  </div>;
}
