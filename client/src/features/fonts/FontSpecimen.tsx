/**
 * 字样：在字体详情里点开一个字形后显示的界面（类似桌面字体查看器）。浏览器读取该字形的字体文件（`file_url`），以临时家族名注册为
 * `FontFace`，再用它排出标题（字体名与字形）、小写与大写字母、数字与标点、一行中文，以及同一句示例文字从小到大的字号阶梯；示例句
 * 默认按显示名是否含中文选中文或英文，可以改成自己的文字。读取期间显示加载中，失败可重试；返回、换字形或关闭对话框时移除注册的字体。
 */
import { useEffect, useState } from "react";
import { ChevronLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { apiBase } from "@/lib/api-base";
import type { FontFaceInfo, FontInfo } from "./api";
import { SLOT_NAMES } from "./format";

/** 在页面里注册一个字体文件，返回 CSS 可用的家族名与移除它的函数；文件读不到或无法解码时拒绝。 */
export type FaceLoader = (url: string) => Promise<{ family: string; release: () => void }>;

/** 默认的注册方式：用不会与字体库重名的临时家族名把文件注册为 `FontFace`。 */
export const loadFontFile: FaceLoader = async (url) => {
  const family = `imv-specimen-${Math.random().toString(36).slice(2)}`;
  const face = new FontFace(family, `url("${url}")`);
  document.fonts.add(await face.load());
  return { family, release: () => document.fonts.delete(face) };
};

/** 字形的固定展示行：字母、数字与常用标点、一行中文。 */
const LINES = ["abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789.:,;(*!?')", "天地玄黄 宇宙洪荒 日月盈昃 辰宿列张"];
/** 字号阶梯（像素）。 */
const SIZES = [12, 14, 16, 20, 24, 32, 40, 48, 64, 80, 96];
/** 字号阶梯的默认示例句。 */
const SAMPLES = { zh: "我能吞下玻璃而不伤身体。", en: "The wizard quickly jinxed the gnomes before they vaporized." };

/** 一个字形的字样；`onBack` 回到字体详情，`loader` 供测试替换字体文件的注册。 */
export function FontSpecimen({ font, face, onBack, loader = loadFontFile }: {
  font: FontInfo;
  face: FontFaceInfo & { file_url: string };
  onBack: () => void;
  loader?: FaceLoader;
}) {
  const [state, setState] = useState<"loading" | "error" | { family: string }>("loading");
  const [attempt, setAttempt] = useState(0);
  const [sample, setSample] = useState(/[㐀-鿿]/.test(font.label) ? SAMPLES.zh : SAMPLES.en);
  // 注册这个字形的文件；离开或重试前移除，迟到的结果直接释放。
  useEffect(() => {
    let active = true;
    let release = () => {};
    setState("loading");
    loader(apiBase() + face.file_url).then(
      (loaded) => {
        if (!active) return loaded.release();
        release = loaded.release;
        setState({ family: loaded.family });
      },
      () => { if (active) setState("error"); },
    );
    return () => {
      active = false;
      release();
    };
  }, [face.file_url, loader, attempt]);
  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="flex items-center gap-2 border-b pr-8 pb-3">
        <Button type="button" variant="ghost" size="icon" aria-label="返回字体详情" onClick={onBack}>
          <ChevronLeft aria-hidden="true" />
        </Button>
        <div className="min-w-0 flex-1 text-center">
          <DialogTitle className="truncate">{font.label}</DialogTitle>
          <DialogDescription>{SLOT_NAMES[face.slot]}</DialogDescription>
        </div>
      </div>
      {state === "loading" && (
        <p role="status" className="flex items-center gap-2 py-10 text-sm text-muted-foreground"><Spinner />正在读取字体文件…</p>
      )}
      {state === "error" && (
        <div role="alert" className="flex items-center gap-3 py-10 text-sm text-destructive">
          <span>字体文件读取失败，请检查网络后重试。</span>
          <Button type="button" variant="outline" size="sm" onClick={() => setAttempt((count) => count + 1)}>重试</Button>
        </div>
      )}
      {typeof state === "object" && (
        <>
          <Input value={sample} onChange={(event) => setSample(event.target.value)} aria-label="示例文字" placeholder="输入示例文字" />
          <div aria-label="字样" className="min-h-0 overflow-x-hidden overflow-y-auto" style={{ fontFamily: `"${state.family}"` }}>
            <p className="truncate pb-2 leading-tight" style={{ fontSize: 48 }}>{font.label}，{SLOT_NAMES[face.slot]}</p>
            {LINES.map((line) => <p key={line} className="truncate leading-snug" style={{ fontSize: 24 }}>{line}</p>)}
            <div className="mt-4 space-y-1">
              {SIZES.map((size) => (
                <p key={size} title={`${size}px`} className="overflow-hidden leading-tight whitespace-nowrap" style={{ fontSize: size }}>{sample}</p>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
