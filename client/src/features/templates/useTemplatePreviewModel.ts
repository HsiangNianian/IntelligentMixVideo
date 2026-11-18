/** 编辑器和主页补图共享同一份预览时间线、文字绑定及 AI 叠加参数。 */
import { useMemo, useState } from "react";
import type { SpriteAsset } from "@/features/sprites/model";
import { BILINGUAL_PROP, contentLines, transformFields } from "@/features/sprites/parameters";
import { activeDraft, boundText, singleRowText, type Draft, type PreviewMedia } from "./model";
import { previewOpeningDuration } from "./avatarPreview";
import { validateSplitScreen } from "./splitScreen";
import { previewTransitions } from "./transitions";
import { previewContext, resolveTemplate } from "./tracks";
import { clipSlot, hiddenTextClips, timeClips } from "./clips";
import { exampleKeyword } from "./timeline";

/** 从已启用的画面对象生成播放器输入；一次挂载中复用随机转场的选择。 */
export function useTemplatePreviewModel(draft: Draft | null, media: PreviewMedia | undefined, assets: SpriteAsset[] | null) {
  const [transitionRandom, setTransitionRandom] = useState<readonly [number, number]>(() => [Math.random(), Math.random()]);
  const visibleDraft = useMemo(() => draft ? activeDraft(draft) : null, [draft]);
  const previewSelection = useMemo(() => visibleDraft ? previewTransitions(visibleDraft, transitionRandom, assets ?? []) : null, [visibleDraft, transitionRandom, assets]);
  const previewDraft = previewSelection?.draft;
  const preview = useMemo(() => {
    if (!previewDraft || !media) return { timeline: undefined, error: "" };
    try {
      if (previewDraft.split_screen?.enabled) validateSplitScreen(previewDraft.split_screen);
      return { timeline: resolveTemplate(previewDraft, previewContext(media)), error: "" };
    }
    catch (reason) { return { timeline: undefined, error: reason instanceof Error ? reason.message : "时间规则无法应用到当前视频" }; }
  }, [previewDraft, media]);
  const previewLength = preview.timeline?.duration ?? 0;
  const previewClips = useMemo(() => previewDraft && preview.timeline ? timeClips(previewDraft, preview.timeline).clips : [], [previewDraft, preview.timeline]);
  // 所作用文字在当前预览中不显示时，字效也不显示，避免按原有时间出现在画面上。
  const hiddenClips = useMemo(() => previewDraft && preview.timeline ? hiddenTextClips(previewDraft, preview.timeline) : new Set<string>(), [previewDraft, preview.timeline]);
  const draftTracks = draft?.tracks;
  const spriteClips = useMemo(() => previewClips.map((clip) => {
    const asset = assets?.find((item) => item.id === clip.sprite_id);
    // 作用于文字的字效显示该文字对象的示例文字与关键词，合成时由总线换成实际标题或切片；英文固定单行，双栏字效只用上栏。
    const text = boundText(draftTracks ?? [], clip);
    // 双语字效（双语字幕）的下栏是所作用对象英文文字对象的示例译文，与合成注入同一句译文一致。
    const english = asset?.bilingual && clip.text_target ? draftTracks?.find((track) => track.target === clip.text_target && track.language === "en") : undefined;
    const point = previewDraft?.split_screen?.enabled && clip.text_target === "subtitle" ? previewDraft.split_screen[text?.language === "en" ? "subtitleEn" : "subtitle"] : undefined;
    // 同屏沿用普通字效的内容框中心，框高与缩放不参与位置换算。
    const transform = point && asset ? { ...clip.transform, x: point.x * asset.width / 100, y: point.y * asset.height / 100 } : clip.transform;
    return {
      id: clip.id,
      spriteId: clip.sprite_id,
      sourceVersionId: asset?.sourceVersionId,
      aspect: asset?.aspect ?? 9 / 16,
      name: asset?.name ?? "AI 资产",
      start: clip.start,
      end: Math.min(previewLength, clip.start + (clip.duration ?? asset?.seconds ?? 0)),
      length: clip.duration ?? asset?.seconds ?? 0,
      slot: clipSlot(clip, asset),
      textTrackId: text?.id,
      boundary: clip.transition_boundary,
      values: text && clip.text_target ? {
        ...clip.parameters,
        text: text.language === "en" ? singleRowText(text.editor[clip.text_target]) : text.editor[clip.text_target],
        keywords: [exampleKeyword(text.editor, clip.text_target, text.language)].filter(Boolean),
        ...(asset?.bilingual && clip.text_target ? { [BILINGUAL_PROP]: english ? contentLines(asset) > 1 ? english.editor[clip.text_target] : singleRowText(english.editor[clip.text_target]) : "",
          lineStartFrames: Array(asset.lines ?? 2).fill(0) } : {}),
      } : clip.parameters,
      transform,
      opening: point ? { end: previewOpeningDuration(previewDraft?.avatar_effects, previewLength), transform: clip.transform } : undefined,
      bilingual: asset?.bilingual === true,
      canvas: asset ? { width: asset.width, height: asset.height, box: asset.box, movable: !point && transformFields(asset.kind).includes("x") } : undefined,
    };
  }).filter((clip) => clip.end > clip.start && !hiddenClips.has(clip.id)), [previewClips, hiddenClips, draftTracks, assets, previewLength, previewDraft?.split_screen, previewDraft?.avatar_effects]);
  const coverAssetsReady = previewClips.every(clip => assets?.some(asset => asset.id === clip.sprite_id));
  return { hiddenClips, visibleDraft, previewSelection, previewDraft, preview, previewLength, spriteClips, coverAssetsReady, setTransitionRandom };
}
