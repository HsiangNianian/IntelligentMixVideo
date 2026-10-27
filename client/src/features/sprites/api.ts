/** Sprite 资产 HTTP 边界：发布、重命名与目录使用 Protobuf 二进制，删除无请求体，目录变化经 SSE 推送；写入失败不自动重试。片段随云端模板保存，不在这里。 */
import { updateFetch as fetch } from "@/lib/update-guard";
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { apiBase } from "@/lib/api-base";
import { deviceId } from "@/features/remotion_templates/localSessions";
import {
  ListSpritesResponseSchema,
  PublishSpriteRequestSchema,
  PublishSpriteResponseSchema,
  RenameSpriteRequestSchema,
  RenameSpriteResponseSchema,
  type SpriteKind,
  type SpriteSummary,
} from "@/generated/imv/sprite/v1/sprite_pb";
import type { SpriteAsset } from "./model";
import { assetParameter } from "./parameters";

/** 一次发布选择：文字类型必须指明接收业务文字（和可选关键词）的参数字段。 */
export interface PublishChoice {
  kind: SpriteKind;
  textProp: string;
  keywordsProp: string;
}

/** 发布要在隔离浏览器里渲染参数边界样本，比目录读取慢得多。 */
const PUBLISH_TIMEOUT_MS = 180_000;

/** 服务端答复的 HTTP 错误：message 是可展示的服务端说明，status 供调用方区分「已不存在」等情况。 */
export class SpriteHttpError extends Error {
  /** 状态码与展示文字一起保留。 */
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** 有界请求：调用方取消优先，超时给出可重试提示；HTTP 错误展示服务端 detail。 */
async function request(path: string, method: "GET" | "POST" | "DELETE", signal?: AbortSignal, body?: Uint8Array, timeoutMs = 20_000, device?: string): Promise<Uint8Array> {
  const controller = new AbortController();
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  try {
    const response = await fetch(`${apiBase()}${path}`, {
      method,
      headers: {
        Accept: "application/x-protobuf",
        ...(body ? { "Content-Type": "application/x-protobuf" } : {}),
        ...(device ? { "X-IMV-Device": device } : {}),
      },
      body: body ? Uint8Array.from(body) : undefined,
      signal: controller.signal,
    });
    if (!response.ok) {
      const { detail } = (await response.json().catch(() => ({}))) as { detail?: unknown };
      throw new SpriteHttpError(response.status, typeof detail === "string" ? detail : `请求未完成（HTTP ${response.status}）`);
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
    sourceVersionId: summary.sourceVersionId,
    seconds: summary.canvas && summary.canvas.fps ? summary.canvas.previewFrames / summary.canvas.fps : 0,
    aspect: summary.canvas && summary.canvas.height ? summary.canvas.width / summary.canvas.height : 9 / 16,
    width: summary.canvas?.width ?? 0,
    height: summary.canvas?.height ?? 0,
    fps: summary.canvas?.fps ?? 0,
    box: summary.contentBox && summary.contentBox.width > 0 && summary.contentBox.height > 0
      ? { x: summary.contentBox.x, y: summary.contentBox.y, width: summary.contentBox.width, height: summary.contentBox.height }
      : { x: 0, y: 0, width: summary.canvas?.width ?? 0, height: summary.canvas?.height ?? 0 },
    parameters: summary.parameters.map(assetParameter).filter((item) => item !== null),
    lines: summary.lineCount,
  };
}

/** 读取可供模板编辑添加的发布目录。 */
export async function listSprites(signal?: AbortSignal): Promise<SpriteAsset[]> {
  return fromBinary(ListSpritesResponseSchema, await request("/api/sprites", "GET", signal)).sprites.map(toAsset);
}

/**
 * 把本机会话的成功版本保存为资产（服务端先渲染参数边界样本）；相同选择重复调用返回原资产。
 * 提供 `target` 时覆盖该资产（同一 sprite_id 与名称，模板里的片段随即使用新版本），否则保存为新资产。
 */
export async function publishSprite(versionId: string, choice: PublishChoice, target?: string): Promise<SpriteAsset> {
  const body = toBinary(PublishSpriteRequestSchema, create(PublishSpriteRequestSchema, {
    sourceVersionId: versionId,
    ...choice,
    ...(target ? { targetSpriteId: target } : {}),
  }));
  const device = await deviceId();
  const { sprite } = fromBinary(PublishSpriteResponseSchema, await request("/api/sprites/publish", "POST", undefined, body, PUBLISH_TIMEOUT_MS, device));
  if (!sprite) throw new Error("发布响应缺少内容");
  return toAsset(sprite);
}

/** 目录推送空闲时每 15 秒有一条 ping；超过这个时长什么都没收到，就当连接已失效（网络切换、代理断开）并重新连接。 */
export const CATALOG_SILENCE_MS = 45_000;

/**
 * 订阅目录变化：服务端在连接时与之后任何人保存、改名或删除资产时推送 `catalog.changed`，每条都调用 `onChange` 重新读取目录。
 * 断线由浏览器自动重连，重连后的第一条事件同样触发读取，断线期间的变化不会丢；静默超过 `CATALOG_SILENCE_MS` 时换一条新连接。
 * 返回停止订阅的函数。
 */
export function watchCatalog(onChange: () => void): () => void {
  let source: EventSource | undefined;
  let silence: ReturnType<typeof setTimeout> | undefined;
  /** 收到任何事件都说明连接仍有效，重新计时。 */
  function alive() {
    clearTimeout(silence);
    silence = setTimeout(connect, CATALOG_SILENCE_MS);
  }
  /** 建立连接，替换已失效的旧连接。 */
  function connect() {
    source?.close();
    source = new EventSource(`${apiBase()}/api/sprites/stream`);
    source.addEventListener("ping", alive);
    source.addEventListener("catalog.changed", () => {
      alive();
      onChange();
    });
    alive();
  }
  connect();
  return () => {
    clearTimeout(silence);
    source?.close();
  };
}

/**
 * 删除未被任何云端模板使用的资产；被使用时服务端说明涉及的模板，删除失败不自动重试。
 * 资产已不存在（其他人删掉了，或上一次删除其实已经完成）时目标已达成，返回 false 而不是报错。
 */
export async function deleteSprite(id: string): Promise<boolean> {
  try {
    await request(`/api/sprites/${encodeURIComponent(id)}`, "DELETE");
    return true;
  } catch (error) {
    if (error instanceof SpriteHttpError && error.status === 404) return false;
    throw error;
  }
}

/** 修改资产在目录里显示的名称；服务端去除首尾空白并限制 1～100 个字符，不要求唯一。被模板使用的资产也可以改名，失败不自动重试。 */
export async function renameSprite(id: string, name: string): Promise<SpriteAsset> {
  const body = toBinary(RenameSpriteRequestSchema, create(RenameSpriteRequestSchema, { name }));
  const { sprite } = fromBinary(RenameSpriteResponseSchema, await request(`/api/sprites/${encodeURIComponent(id)}/rename`, "POST", undefined, body));
  if (!sprite) throw new Error("重命名响应缺少内容");
  return toAsset(sprite);
}
