/**
 * 新会话发送前的字效时长选择：常用秒数下拉加自定义输入，默认 5 秒。
 * 只在首次请求前显示；通过 `onChange` 上报整帧数，输入无效时上报 null，由聊天面板禁止发送。
 */
import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DEFAULT_DURATION_SECONDS, DURATION_PRESETS, MAX_DURATION_SECONDS, durationFrames } from "./composition";

const CUSTOM = "custom";

interface Props {
  disabled: boolean;
  onChange: (frames: number | null) => void;
}

/** 下拉选常用时长或「自定义」；自定义输入按秒解析并换算为整帧，范围外显示提示。 */
export function DurationPicker({ disabled, onChange }: Props) {
  const [choice, setChoice] = useState(String(DEFAULT_DURATION_SECONDS));
  const [custom, setCustom] = useState("");
  const invalid = choice === CUSTOM && custom !== "" && durationFrames(Number(custom)) === null;
  /** 选择预设时立即上报；切到自定义时按已输入内容上报，空输入视为无效。 */
  function choose(value: string) {
    setChoice(value);
    onChange(durationFrames(value === CUSTOM ? (custom === "" ? NaN : Number(custom)) : Number(value)));
  }
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1">
      <Select value={choice} disabled={disabled} onValueChange={choose}>
        <SelectTrigger size="sm" aria-label="字效时长" className="h-8 w-24 border-none bg-transparent text-xs shadow-none">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {DURATION_PRESETS.map((seconds) => <SelectItem key={seconds} value={String(seconds)}>{seconds} 秒</SelectItem>)}
          <SelectItem value={CUSTOM}>自定义</SelectItem>
        </SelectContent>
      </Select>
      {choice === CUSTOM && (
        <>
          <Input
            type="number"
            inputMode="decimal"
            min={0.1}
            max={MAX_DURATION_SECONDS}
            step="any"
            aria-label="自定义时长（秒）"
            aria-invalid={invalid}
            placeholder="秒"
            disabled={disabled}
            value={custom}
            className="h-8 w-20 text-xs"
            onChange={(event) => {
              setCustom(event.target.value);
              onChange(event.target.value === "" ? null : durationFrames(Number(event.target.value)));
            }}
          />
          {invalid && <span role="alert" className="basis-full text-xs text-destructive">请输入 0.1～{MAX_DURATION_SECONDS} 秒</span>}
        </>
      )}
    </div>
  );
}
