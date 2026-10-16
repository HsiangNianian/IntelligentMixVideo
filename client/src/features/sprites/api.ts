/** Sprite 资产 HTTP 边界：发布、重命名与目录使用 Protobuf 二进制，删除无请求体；写入失败不自动重试。片段随云端模板保存，不在这里。 */
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

/** 把本机会话的成功版本保存为资产（服务端先渲染参数边界样本）；相同选择重复调用返回原资产。 */
export async function publishSprite(versionId: string, choice: PublishChoice): Promise<SpriteAsset> {
  const body = toBinary(PublishSpriteRequestSchema, create(PublishSpriteRequestSchema, { sourceVersionId: versionId, ...choice }));
  const device = await deviceId();
  const { sprite } = fromBinary(PublishSpriteResponseSchema, await request("/api/sprites/publish", "POST", undefined, body, PUBLISH_TIMEOUT_MS, device));
  if (!sprite) throw new Error("发布响应缺少内容");
  return toAsset(sprite);
}

/** 删除未被任何云端模板使用的资产；被使用时服务端说明涉及的模板，删除失败不自动重试。 */
export async function deleteSprite(id: string): Promise<void> {
  await request(`/api/sprites/${encodeURIComponent(id)}`, "DELETE");
}

/** 修改资产在目录里显示的名称；服务端去除首尾空白并限制 1～100 个字符，不要求唯一。被模板使用的资产也可以改名，失败不自动重试。 */
export async function renameSprite(id: string, name: string): Promise<SpriteAsset> {
  const body = toBinary(RenameSpriteRequestSchema, create(RenameSpriteRequestSchema, { name }));
  const { sprite } = fromBinary(RenameSpriteResponseSchema, await request(`/api/sprites/${encodeURIComponent(id)}/rename`, "POST", undefined, body));
  if (!sprite) throw new Error("重命名响应缺少内容");
  return toAsset(sprite);
}
