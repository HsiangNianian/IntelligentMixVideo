/** Sprite 资产 HTTP 边界：发布、目录与 Remotion 片段保存均使用 Protobuf 二进制；写入失败不自动重试。 */
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { apiBase } from "@/lib/api-base";
import {
  GetStyleSpritesResponseSchema,
  ListSpritesResponseSchema,
  PublishSpriteRequestSchema,
  PublishSpriteResponseSchema,
  SaveStyleSpritesRequestSchema,
  SaveStyleSpritesResponseSchema,
  type SpriteKind,
  type SpriteSummary,
  type StyleSpriteBindings,
} from "@/generated/imv/sprite/v1/sprite_pb";
import type { Placement, SpriteAsset } from "./model";

/** 一次发布选择：文字类型必须指明接收业务文字（和可选关键词）的参数字段。 */
export interface PublishChoice {
  kind: SpriteKind;
  textProp: string;
  keywordsProp: string;
}

/** 已保存的 Remotion 片段与其乐观锁版本。 */
export interface Bindings {
  placements: Placement[];
  revision: bigint;
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

/** 读取可供模板编辑添加的发布目录。 */
export async function listSprites(signal?: AbortSignal): Promise<SpriteAsset[]> {
  return fromBinary(ListSpritesResponseSchema, await request("/api/sprites", "GET", signal)).sprites.map(toAsset);
}

/** 把成功版本保存为资产；相同选择重复调用返回原资产。 */
export async function publishSprite(versionId: string, choice: PublishChoice): Promise<SpriteAsset> {
  const body = toBinary(PublishSpriteRequestSchema, create(PublishSpriteRequestSchema, { sourceVersionId: versionId, ...choice }));
  const { sprite } = fromBinary(PublishSpriteResponseSchema, await request("/api/sprites/publish", "POST", undefined, body));
  if (!sprite) throw new Error("发布响应缺少内容");
  return toAsset(sprite);
}

/** 还原已保存的片段；服务端按 order 保存的顺序即数组顺序。 */
function toBindings(message: StyleSpriteBindings | undefined): Bindings {
  if (!message) throw new Error("绑定响应缺少内容");
  return {
    revision: message.revision,
    placements: [...message.placements].sort((a, b) => a.order - b.order).map((item) => ({
      id: item.id,
      spriteId: item.spriteId,
      start: item.start,
      duration: item.duration ?? 0,
    })),
  };
}

/** 读取 style_id 下的 Remotion 片段；从未保存过时为空列表和版本 0。 */
export async function getBindings(styleId: string, signal?: AbortSignal): Promise<Bindings> {
  const data = await request(`/api/sprites/styles/${encodeURIComponent(styleId)}`, "GET", signal);
  return toBindings(fromBinary(GetStyleSpritesResponseSchema, data).bindings);
}

/** 整体替换 style_id 下的 Remotion 片段，只写资产、秒起点、固定时长与顺序；expectedRevision 过期时服务端返回冲突。 */
export async function saveBindings(styleId: string, placements: Placement[], expectedRevision: bigint): Promise<Bindings> {
  const message = create(SaveStyleSpritesRequestSchema, {
    styleId,
    expectedRevision,
    placements: placements.map((item, order) => ({
      id: item.id,
      spriteId: item.spriteId,
      startMode: "seconds",
      start: item.start,
      duration: item.duration,
      order,
    })),
  });
  const data = await request(`/api/sprites/styles/${encodeURIComponent(styleId)}`, "POST", undefined, toBinary(SaveStyleSpritesRequestSchema, message));
  return toBindings(fromBinary(SaveStyleSpritesResponseSchema, data).bindings);
}
