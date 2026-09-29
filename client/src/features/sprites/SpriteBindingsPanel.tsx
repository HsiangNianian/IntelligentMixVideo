/** Cloud-style Sprite asset picker and placement editor; business copy and actual timing stay bus-owned. */
import { useId, useState } from "react";
import { create } from "@bufbuild/protobuf";
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
import { apiBase } from "@/lib/api-base";
import {
  OperatorAccess,
  ScalarValueSchema,
  SpriteKind,
  SpritePlacementSchema,
  SpriteParameterOverrideSchema,
  SpriteTarget,
  type ScalarValue,
  type SpriteParameter,
  type SpritePlacement,
  type SpriteSummary,
} from "@/generated/imv/sprite/v1/sprite_pb";
import { sampleCopy, type SpritePreviewCopy } from "./preview";

const targets: Record<number, { value: SpriteTarget; label: string }[]> = {
  [SpriteKind.TEXT]: [
    { value: SpriteTarget.TITLE, label: "标题" },
    { value: SpriteTarget.SUBTITLE, label: "字幕与关键词" },
  ],
  [SpriteKind.FILTER_OVERLAY]: [
    { value: SpriteTarget.FILTER, label: "滤镜叠加" },
  ],
  [SpriteKind.VIDEO_OVERLAY]: [
    { value: SpriteTarget.VIDEO_EFFECT, label: "持续视频动效" },
    { value: SpriteTarget.VIDEO_ENTER, label: "片段入场" },
    { value: SpriteTarget.VIDEO_EXIT, label: "片段出场" },
  ],
  [SpriteKind.TRANSITION_OVERLAY]: [
    { value: SpriteTarget.TRANSITION, label: "素材转场" },
  ],
};

/** Reassign display and IMS video-track order after removing or moving a placement. */
function ordered(items: SpritePlacement[]): SpritePlacement[] {
  return items.map((item, order) =>
    create(SpritePlacementSchema, { ...item, order }),
  );
}

/** Read an override or its published default without granting access to runtime copy fields. */
function valueFor(
  placement: SpritePlacement,
  parameter: SpriteParameter,
): ScalarValue | undefined {
  return (
    placement.overrides.find((item) => item.key === parameter.key)?.value ??
    parameter.defaultValue
  );
}

/** One editable scalar control; invalid intermediate number input stays in the form until corrected. */
function ParameterInput({
  placement,
  parameter,
  onChange,
}: {
  placement: SpritePlacement;
  parameter: SpriteParameter;
  onChange: (value: ScalarValue) => void;
}) {
  const id = useId();
  const value = valueFor(placement, parameter)?.value;
  const readOnly = parameter.access !== OperatorAccess.VISIBLE_EDITABLE;
  if (parameter.allowedValues.length)
    return (
      <div className="space-y-1">
        <Label htmlFor={id}>{parameter.label}</Label>
        <Select
          value={value ? String(value.value) : undefined}
          disabled={readOnly}
          onValueChange={(selected) => {
            const choice = parameter.allowedValues.find(
              (item) => item.value.case === value?.case && String(item.value.value) === selected,
            );
            if (choice) onChange(choice);
          }}
        >
          <SelectTrigger id={id} className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            {parameter.allowedValues.map((choice) => (
              <SelectItem key={String(choice.value.value)} value={String(choice.value.value)}>
                {({ left: "左对齐", center: "居中", right: "右对齐", "400": "常规", "700": "粗体" } as Record<string, string>)[String(choice.value.value)] ?? String(choice.value.value)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    );
  if (value?.case === "boolValue")
    return (
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="checkbox"
          checked={value.value}
          disabled={readOnly}
          onChange={(event) =>
            onChange(
              create(ScalarValueSchema, {
                value: { case: "boolValue", value: event.target.checked },
              }),
            )
          }
        />
        <Label htmlFor={id}>{parameter.label}</Label>
      </div>
    );
  if (value?.case === "numberValue")
    return (
      <div className="space-y-1">
        <Label htmlFor={id}>{parameter.label}</Label>
        <Input
          key={`${placement.id}:${parameter.key}`}
          id={id}
          type="number"
          step="any"
          min={parameter.minimum}
          max={parameter.maximum}
          defaultValue={value.value}
          disabled={readOnly}
          onBlur={(event) => {
            if (event.target.validity.valid && event.target.value.trim())
              onChange(
                create(ScalarValueSchema, {
                  value: {
                    case: "numberValue",
                    value: Number(event.target.value),
                  },
                }),
              );
          }}
        />
      </div>
    );
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{parameter.label}</Label>
      <Input
        key={`${placement.id}:${parameter.key}`}
        id={id}
        defaultValue={value?.case === "stringValue" ? value.value : ""}
        disabled={readOnly}
        onBlur={(event) =>
          onChange(
            create(ScalarValueSchema, {
              value: { case: "stringValue", value: event.target.value },
            }),
          )
        }
      />
    </div>
  );
}

/** Add one published Sprite from the template editor's asset column. */
export function SpriteAssetPicker({
  catalog,
  count,
  loading,
  onAdd,
}: {
  catalog: SpriteSummary[];
  count: number;
  loading: boolean;
  onAdd: (placement: SpritePlacement) => void;
}) {
  const [selected, setSelected] = useState("");
  const chosen = catalog.find((item) => item.spriteId === selected);
  return (
    <section aria-label="Remotion Sprite 资产" className="space-y-3 border-b p-4">
      <h3 className="text-sm font-semibold">Remotion Sprite 特效</h3>
      <p className="text-xs text-muted-foreground">从字效工作区发布后，可添加到当前云端模板。</p>
      <Select value={selected} onValueChange={setSelected} disabled={loading || catalog.length === 0}>
        <SelectTrigger className="w-full min-w-0" aria-label="选择 Sprite">
          <SelectValue placeholder={loading ? "正在读取 Sprite…" : catalog.length ? "选择已发布 Sprite" : "暂无已发布 Sprite"} />
        </SelectTrigger>
        <SelectContent>
          {catalog.map((item) => <SelectItem key={item.spriteId} value={item.spriteId}>{item.name}</SelectItem>)}
        </SelectContent>
      </Select>
      <Button
        type="button"
        variant="outline"
        className="w-full"
        disabled={!chosen || count >= 100}
        onClick={() => {
          if (!chosen) return;
          onAdd(create(SpritePlacementSchema, {
            id: crypto.randomUUID(),
            spriteId: chosen.spriteId,
            target: targets[chosen.kind]?.[0]?.value ?? SpriteTarget.UNSPECIFIED,
            startMode: "seconds",
            start: 0,
            duration: chosen.kind === SpriteKind.TRANSITION_OVERLAY ? 1 : undefined,
            order: count,
          }));
        }}
      >
        添加 Sprite
      </Button>
    </section>
  );
}

/** Edit added Sprite placements and style values; sample videos remain read-only. */
export function SpriteBindingsPanel({
  catalog,
  placements,
  onChange,
  open,
  onOpenChange,
  previewCopy,
  onPreviewCopyChange,
}: {
  catalog: SpriteSummary[];
  placements: SpritePlacement[];
  onChange: (next: SpritePlacement[]) => void;
  open: string | null;
  onOpenChange: (id: string | null) => void;
  previewCopy: Record<string, SpritePreviewCopy>;
  onPreviewCopyChange: (id: string, value: SpritePreviewCopy) => void;
}) {
  const [error, setError] = useState("");

  /** Replace one placement while keeping the independent IMS template draft untouched. */
  function replace(
    id: string,
    update: (value: SpritePlacement) => SpritePlacement,
  ) {
    onChange(
      ordered(placements.map((item) => (item.id === id ? update(item) : item))),
    );
  }
  /** Write a scalar override only when it differs from the published default. */
  function setParameter(
    placement: SpritePlacement,
    parameter: SpriteParameter,
    value: ScalarValue,
  ) {
    const unchanged =
      JSON.stringify(value.value) ===
      JSON.stringify(parameter.defaultValue?.value);
    replace(placement.id, (item) =>
      create(SpritePlacementSchema, {
        ...item,
        overrides: [
          ...item.overrides.filter((entry) => entry.key !== parameter.key),
          ...(!unchanged
            ? [
                create(SpriteParameterOverrideSchema, {
                  key: parameter.key,
                  value,
                }),
              ]
            : []),
        ],
      }),
    );
  }
  /** Parse timing after the user finishes editing; the bus later intersects business intervals. */
  function setNumber(id: string, key: "start" | "duration", raw: string) {
    const value = raw.trim() ? Number(raw) : undefined;
    if (
      value !== undefined &&
      (!Number.isFinite(value) || value < (key === "duration" ? 0.001 : 0))
    ) {
      setError("Sprite 时间须为非负有限数，固定时长须大于零");
      return;
    }
    setError("");
    replace(id, (item) =>
      create(SpritePlacementSchema, {
        ...item,
        [key]: value ?? (key === "start" ? 0 : undefined),
      }),
    );
  }
  return (
    <section
      aria-label="已添加的 Remotion Sprite"
      className="space-y-4 border-t p-4"
    >
      <div>
        <h3 className="text-sm font-semibold">已添加的 Remotion Sprite</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          发布版本提供样式；标题、字幕、关键词与真实时间由总线填入。
        </p>
      </div>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {!placements.length && <p className="text-sm text-muted-foreground">从左侧特效资产选择并添加 Sprite。</p>}
      {placements.map((placement, index) => {
        const sprite = catalog.find(
          (item) => item.spriteId === placement.spriteId,
        );
        const options = (targets[sprite?.kind ?? 0] ?? []).filter(
          (item) =>
            item.value !== SpriteTarget.SUBTITLE || sprite?.keywordsSupported,
        );
        const atBoundary = [
          SpriteTarget.TRANSITION,
          SpriteTarget.VIDEO_ENTER,
          SpriteTarget.VIDEO_EXIT,
        ].includes(placement.target);
        const example = previewCopy[placement.id] ?? sampleCopy(placement.target);
        return (
          <article
            key={placement.id}
            className="space-y-3 rounded-lg border bg-background p-3"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <button
                type="button"
                className="text-left text-sm font-medium underline-offset-2 hover:underline"
                onClick={() =>
                  onOpenChange(open === placement.id ? null : placement.id)
                }
              >
                {index + 1}. {sprite?.name ?? "未找到的 Sprite"}
              </button>
              <div className="flex gap-1">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={index === 0}
                  onClick={() => {
                    const next = [...placements];
                    [next[index - 1], next[index]] = [
                      next[index],
                      next[index - 1],
                    ];
                    onChange(ordered(next));
                  }}
                >
                  上移
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={index === placements.length - 1}
                  onClick={() => {
                    const next = [...placements];
                    [next[index], next[index + 1]] = [
                      next[index + 1],
                      next[index],
                    ];
                    onChange(ordered(next));
                  }}
                >
                  下移
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    onChange(ordered(placements.filter((item) => item.id !== placement.id)));
                    if (open === placement.id) onOpenChange(null);
                  }}
                >
                  移除
                </Button>
              </div>
            </div>
            {open === placement.id && (
              <div className="space-y-3">
                {sprite?.previewUrl && (
                  <video
                    aria-label={`${sprite.name} 示例预览`}
                    src={`${apiBase()}${sprite.previewUrl}`}
                    controls
                    muted
                    loop
                    className="max-h-56 w-full rounded bg-muted object-contain"
                  />
                )}
                {sprite?.kind === SpriteKind.TEXT && <div className="space-y-3 rounded-md border border-dashed p-3">
                    <p className="text-xs text-muted-foreground">以下文字和关键词只用于实时预览，不保存到模板；实际业务数据由总线传入。</p>
                    <div className="space-y-1">
                      <Label htmlFor={`${placement.id}-sample-text`}>预览示例文字</Label>
                      <Input id={`${placement.id}-sample-text`} value={example.text} maxLength={2000}
                        onChange={(event) => onPreviewCopyChange(placement.id, { ...example, text: event.target.value })} />
                    </div>
                    {placement.target === SpriteTarget.SUBTITLE && <div className="space-y-1">
                      <Label htmlFor={`${placement.id}-sample-keywords`}>预览关键词（逗号分隔）</Label>
                      <Input id={`${placement.id}-sample-keywords`} value={example.keywords}
                        onChange={(event) => onPreviewCopyChange(placement.id, { ...example, keywords: event.target.value })} />
                    </div>}
                  </div>}
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1">
                    <Label>作用对象</Label>
                    <Select
                      value={String(placement.target)}
                      onValueChange={(value) => {
                        const target = Number(value) as SpriteTarget;
                        const boundary = [
                          SpriteTarget.TRANSITION,
                          SpriteTarget.VIDEO_ENTER,
                          SpriteTarget.VIDEO_EXIT,
                        ].includes(target);
                        replace(placement.id, (item) =>
                          create(SpritePlacementSchema, {
                            ...item,
                            target,
                            ...(boundary
                              ? {
                                  startMode: "seconds",
                                  start: 0,
                                  duration: item.duration ?? 1,
                                }
                              : {}),
                          }),
                        );
                      }}
                    >
                      <SelectTrigger aria-label="Sprite 作用对象">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {options.map((item) => (
                          <SelectItem
                            key={item.value}
                            value={String(item.value)}
                          >
                            {item.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  {!atBoundary && (
                    <div className="space-y-1">
                      <Label>开始方式</Label>
                      <Select
                        value={placement.startMode}
                        onValueChange={(value) =>
                          replace(placement.id, (item) =>
                            create(SpritePlacementSchema, {
                              ...item,
                              startMode: value,
                              start: 0,
                            }),
                          )
                        }
                      >
                        <SelectTrigger aria-label="Sprite 开始方式">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="seconds">秒数</SelectItem>
                          <SelectItem value="percent">百分比</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  )}
                  {!atBoundary && (
                    <div className="space-y-1">
                      <Label htmlFor={`${placement.id}-start`}>开始位置</Label>
                      <Input
                        key={`${placement.id}:${placement.startMode}:start`}
                        id={`${placement.id}-start`}
                        type="number"
                        min="0"
                        max={
                          placement.startMode === "percent"
                            ? "99.9999"
                            : undefined
                        }
                        step="any"
                        defaultValue={placement.start}
                        onBlur={(event) => {
                          if (event.target.validity.valid)
                            setNumber(
                              placement.id,
                              "start",
                              event.target.value,
                            );
                        }}
                      />
                    </div>
                  )}
                  <div className="space-y-1">
                    <Label htmlFor={`${placement.id}-duration`}>
                      {atBoundary
                        ? "边界特效时长（秒）"
                        : "固定时长（留空直到结束）"}
                    </Label>
                    <Input
                      key={`${placement.id}:duration`}
                      id={`${placement.id}-duration`}
                      type="number"
                      required={atBoundary}
                      min="0.001"
                      max="3600"
                      step="any"
                      defaultValue={placement.duration ?? ""}
                      onBlur={(event) => {
                        if (event.target.validity.valid)
                          setNumber(
                            placement.id,
                            "duration",
                            event.target.value,
                          );
                      }}
                    />
                  </div>
                </div>
                {atBoundary && (
                  <p className="text-xs text-muted-foreground">
                    总线在实际素材边界应用此
                    Sprite；预览中的开始位置不参与合成。
                  </p>
                )}
                {!!sprite?.parameters.length && (
                  <div className="grid gap-3 sm:grid-cols-2">
                    {sprite.parameters
                      .filter(
                        (parameter) =>
                          parameter.access !== OperatorAccess.INTERNAL,
                      )
                      .map((parameter) => (
                        <ParameterInput
                          key={parameter.key}
                          placement={placement}
                          parameter={parameter}
                          onChange={(value) =>
                            setParameter(placement, parameter, value)
                          }
                        />
                      ))}
                  </div>
                )}
              </div>
            )}
          </article>
        );
      })}
    </section>
  );
}
