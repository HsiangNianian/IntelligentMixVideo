/** 首次生成的画布与时间设置；仅修改本地草稿，提交前校验并显示整帧后的实际时长。 */
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  canvasPresets,
  compositionSummary,
  resolveComposition,
  type CompositionDraft,
} from "./composition";

/** 折叠低频宽高与帧率设置；禁用时拒绝所有回调，避免提交期间变更已捕获的配置。 */
export function CompositionSettings({
  value,
  disabled,
  onChange,
}: {
  value: CompositionDraft;
  disabled: boolean;
  onChange: (value: CompositionDraft) => void;
}) {
  const id = useId();
  const [advanced, setAdvanced] = useState(false);
  const resolved = resolveComposition(value);
  /** 保留无效中间输入，由统一校验控制发送按钮与错误提示。 */
  function update(patch: Partial<CompositionDraft>) {
    if (!disabled) {
      if (patch.canvas === "custom") setAdvanced(true);
      onChange({ ...value, ...patch });
    }
  }
  return (
    <section
      aria-label="生成配置"
      className="space-y-3 rounded-xl border bg-muted/30 p-3"
    >
      <p className="text-sm font-medium">生成配置</p>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <Label htmlFor={`${id}-canvas`}>画布</Label>
          <Select
            value={value.canvas}
            disabled={disabled}
            onValueChange={(canvas) => {
              if (canvas === "custom") update({ canvas });
              else if (
                Object.prototype.hasOwnProperty.call(canvasPresets, canvas)
              ) {
                const preset = canvas as keyof typeof canvasPresets;
                update({
                  canvas: preset,
                  width: canvasPresets[preset].width,
                  height: canvasPresets[preset].height,
                });
              }
            }}
          >
            <SelectTrigger id={`${id}-canvas`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(canvasPresets).map(([key, preset]) => (
                <SelectItem key={key} value={key}>
                  {preset.label}
                </SelectItem>
              ))}
              <SelectItem value="custom">自定义</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor={`${id}-seconds`}>字效时长（秒）</Label>
          <Input
            id={`${id}-seconds`}
            type="number"
            min={1 / Number(value.fps)}
            max={30}
            step="any"
            disabled={disabled}
            value={value.seconds}
            aria-describedby={`${id}-result`}
            onChange={(event) => update({ seconds: event.target.value })}
          />
        </div>
      </div>
      <div className="flex gap-2" role="group" aria-label="快捷时长">
        {[3, 5, 8].map((seconds) => (
          <Button
            key={seconds}
            type="button"
            size="sm"
            variant="outline"
            disabled={disabled}
            aria-pressed={Number(value.seconds) === seconds}
            onClick={() => update({ seconds: String(seconds) })}
          >
            {seconds} 秒
          </Button>
        ))}
      </div>
      <details
        open={advanced}
        onToggle={(event) => setAdvanced(event.currentTarget.open)}
      >
        <summary className="cursor-pointer text-xs text-muted-foreground">
          高级设置（分辨率、帧率）
        </summary>
        <div className="mt-3 grid grid-cols-2 gap-3">
          {(["width", "height"] as const).map((dimension) => (
            <div key={dimension} className="space-y-2">
              <Label htmlFor={`${id}-${dimension}`}>
                {dimension === "width"
                  ? "画布宽度（像素）"
                  : "画布高度（像素）"}
              </Label>
              <Input
                id={`${id}-${dimension}`}
                type="number"
                min={64}
                max={3840}
                step={2}
                disabled={disabled}
                value={value[dimension]}
                aria-describedby={`${id}-result`}
                onChange={(event) =>
                  update({ canvas: "custom", [dimension]: event.target.value })
                }
              />
            </div>
          ))}
          <div className="col-span-2 space-y-2">
            <Label htmlFor={`${id}-fps`}>帧率</Label>
            <Select
              value={value.fps}
              disabled={disabled}
              onValueChange={(fps) => update({ fps })}
            >
              <SelectTrigger id={`${id}-fps`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[24, 25, 30, 60].map((fps) => (
                  <SelectItem key={fps} value={String(fps)}>
                    {fps} FPS
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </details>
      <p
        id={`${id}-result`}
        role={resolved.error ? "alert" : "status"}
        className={
          resolved.error
            ? "text-xs text-destructive"
            : "text-xs text-muted-foreground"
        }
      >
        {resolved.error || compositionSummary(resolved.composition!)}
      </p>
      <p className="text-xs text-muted-foreground">
        时长按整帧取整，最长 30 秒。创建后固定配置；需要其他规格时新增会话。
      </p>
    </section>
  );
}
