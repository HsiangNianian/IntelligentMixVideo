/**
 * 新会话发送前的预览时长选择：常用秒数下拉加自定义输入，默认 5 秒。字效的最终时长由合成时的渲染请求决定，
 * 这里只决定生成与预览的长度。只在首次请求前显示；通过 `onChange` 上报整帧数，输入无效时上报 null，由聊天面板禁止发送。
 */
import { useId, useState } from "react";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { DEFAULT_DURATION_SECONDS, DURATION_PRESETS, MAX_DURATION_SECONDS, MIN_DURATION_SECONDS, durationFrames } from "./composition";

const CUSTOM = "custom";

interface Props {
  disabled: boolean;
  onChange: (frames: number | null) => void;
}

/** 下拉选常用时长或「自定义」；自定义输入按秒解析并换算为整帧。空输入时说明范围，范围外或无法解析时显示错误，不让发送按钮无故变灰。 */
export function DurationPicker({ disabled, onChange }: Props) {
  const [choice, setChoice] = useState(String(DEFAULT_DURATION_SECONDS));
  const [custom, setCustom] = useState("");
  // 浏览器对「-」「1e」等无法解析的数字输入只给出空值，需从 validity.badInput 区分「未填」和「填错」。
  const [rejected, setRejected] = useState(false);
  const hint = useId();
  const invalid = choice === CUSTOM && (rejected || (custom !== "" && durationFrames(Number(custom)) === null));
  const explained = invalid || custom === "";
  /** 选择预设时立即上报；切到自定义时按已输入内容上报，空输入按 0 秒视为无效。重新挂载的输入框没有残留的错误输入。 */
  function choose(value: string) {
    setChoice(value);
    setRejected(false);
    onChange(durationFrames(Number(value === CUSTOM ? custom : value)));
  }
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1">
      <Select value={choice} disabled={disabled} onValueChange={choose}>
        <SelectTrigger size="sm" aria-label="预览时长" title="只用于生成与预览，最终时长由合成决定" className="h-8 w-24 border-none bg-transparent text-xs shadow-none">
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
            min={MIN_DURATION_SECONDS}
            max={MAX_DURATION_SECONDS}
            step="any"
            aria-label="自定义预览时长（秒）"
            aria-invalid={invalid}
            aria-describedby={explained ? hint : undefined}
            placeholder="秒"
            disabled={disabled}
            value={custom}
            className="h-8 w-20 text-xs"
            onChange={(event) => {
              setCustom(event.target.value);
              onChange(durationFrames(Number(event.target.value)));
            }}
            onInput={(event) => setRejected(event.currentTarget.validity.badInput)}
          />
          {explained && (
            <span id={hint} role={invalid ? "alert" : undefined} className={cn("basis-full text-xs", invalid ? "text-destructive" : "text-muted-foreground")}>
              请输入 {MIN_DURATION_SECONDS}～{MAX_DURATION_SECONDS} 秒
            </span>
          )}
        </>
      )}
    </div>
  );
}
