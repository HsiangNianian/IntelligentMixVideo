/** Sprite 资产 HTTP 边界：资产发布使用 Protobuf；写入失败不自动重试。 */
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { apiBase } from "@/lib/api-base";
import {
  PublishSpriteRequestSchema,
  PublishSpriteResponseSchema,
  type SpriteKind,
  type SpriteSummary,
} from "@/generated/imv/sprite/v1/sprite_pb";
import type { SpriteAsset } from "./model";

/** 一次发布选择：文字类型必须指明接收业务文字（和可选关键词）的参数字段。 */
export interface PublishChoice {
  kind: SpriteKind;
  textProp: string;
  keywordsProp: string;
}

/** 有界请求：调用方取消优先，超时给出可重试提示；HTTP 错误展示服务端 detail。 */
async function request(path: string, method: "GET" | "POST", signal?: AbortSignal, body?: Uint8Array): Promise<Uint8Array> {
  const controller = new AbortController();
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 20_000);
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  try {
    const response = await fetch(`${apiBase()}${path}`, {
      method,
      headers: { Accept: "application/x-protobuf", ...(body ? { "Content-Type": "application/x-protobuf" } : {}) },
      body: body ? Uint8Array.from(body) : undefined,
      signal: controller.signal,
    });
    if (!response.ok) {
      const { detail } = (await response.json().catch(() => ({}))) as { detail?: unknown };
      throw new Error(typeof detail === "string" ? detail : `请求未完成（HTTP ${response.status}）`);
    }
    return new Uint8Array(await response.arrayBuffer());
  } catch (error) {
    if (signal?.aborted) throw error;
    if (timedOut) throw new Error("请求超时，请稍后重试。");
    if (error instanceof TypeError) throw new Error("无法连接服务端，请确认 API 已启动后重试。");
    throw error;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

/** 协议摘要转换为界面使用的目录条目。 */
function toAsset(summary: SpriteSummary): SpriteAsset {
  return {
    id: summary.spriteId,
    name: summary.name,
    kind: summary.kind,
    seconds: summary.canvas && summary.canvas.fps ? summary.canvas.previewFrames / summary.canvas.fps : 0,
    aspect: summary.canvas && summary.canvas.height ? summary.canvas.width / summary.canvas.height : 9 / 16,
  };
}

/** 把成功版本保存为资产；相同选择重复调用返回原资产。 */
export async function publishSprite(versionId: string, choice: PublishChoice): Promise<SpriteAsset> {
  const body = toBinary(PublishSpriteRequestSchema, create(PublishSpriteRequestSchema, { sourceVersionId: versionId, ...choice }));
  const { sprite } = fromBinary(PublishSpriteResponseSchema, await request("/api/sprites/publish", "POST", undefined, body));
  if (!sprite) throw new Error("发布响应缺少内容");
  return toAsset(sprite);
}
