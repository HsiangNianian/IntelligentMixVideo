/** Remotion Sprite binary API; publication and cloud-style bindings stay separate from template protobuf. */
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";
import { apiBase } from "@/lib/api-base";
import {
  GetStyleSpritesResponseSchema,
  ListSpritesResponseSchema,
  PublishSpriteRequestSchema,
  PublishSpriteResponseSchema,
  SaveStyleSpritesRequestSchema,
  SaveStyleSpritesResponseSchema,
  SpriteKind,
  type SpritePlacement,
  type SpriteSummary,
  type StyleSpriteBindings,
} from "@/generated/imv/sprite/v1/sprite_pb";

/** Resolve a binary response with the same API base as the cloud template editor. */
async function request(
  path: string,
  method: "GET" | "POST",
  signal?: AbortSignal,
  body?: Uint8Array,
): Promise<Uint8Array> {
  const response = await fetch(`${apiBase()}${path}`, {
    method,
    signal,
    ...(body
      ? {
          headers: { "Content-Type": "application/x-protobuf" },
          body: Uint8Array.from(body),
        }
      : {}),
  });
  if (!response.ok) {
    let detail = "Sprite 请求失败";
    try {
      const message = await response.json();
      if (typeof message.detail === "string") detail = message.detail;
    } catch {
      /* Keep the stable fallback for non-JSON upstream errors. */
    }
    throw new Error(`${detail}（HTTP ${response.status}）`);
  }
  return new Uint8Array(await response.arrayBuffer());
}

/** List only releases whose source has passed server-side publication checks. */
export async function listSprites(
  signal?: AbortSignal,
): Promise<SpriteSummary[]> {
  const data = await request("/api/sprites", "GET", signal);
  return fromBinary(ListSpritesResponseSchema, data).sprites;
}

/** Publish one accepted Agent version with the same semantic kind as its source. */
export async function publishSprite(
  sourceVersionId: string,
  kind:
    | "text"
    | "subtitle"
    | "filter_overlay"
    | "video_overlay"
    | "transition_overlay",
): Promise<SpriteSummary> {
  const kinds = {
    text: SpriteKind.TEXT,
    subtitle: SpriteKind.TEXT,
    filter_overlay: SpriteKind.FILTER_OVERLAY,
    video_overlay: SpriteKind.VIDEO_OVERLAY,
    transition_overlay: SpriteKind.TRANSITION_OVERLAY,
  };
  const body = toBinary(
    PublishSpriteRequestSchema,
    create(PublishSpriteRequestSchema, {
      sourceVersionId,
      kind: kinds[kind],
      textProp: kind === "text" || kind === "subtitle" ? "0_text" : "",
      keywordsProp: kind === "subtitle" ? "highlightRanges" : "",
    }),
  );
  const data = await request("/api/sprites/publish", "POST", undefined, body);
  const sprite = fromBinary(PublishSpriteResponseSchema, data).sprite;
  if (!sprite) throw new Error("发布响应缺少 Sprite");
  return sprite;
}

/** Read one style's independent Sprite binding revision. */
export async function getStyleSprites(
  styleId: string,
  signal?: AbortSignal,
): Promise<StyleSpriteBindings> {
  const data = await request(
    `/api/sprites/styles/${encodeURIComponent(styleId)}`,
    "GET",
    signal,
  );
  const bindings = fromBinary(GetStyleSpritesResponseSchema, data).bindings;
  if (!bindings) throw new Error("Sprite 绑定响应缺少数据");
  return bindings;
}

/** Replace one style's placements only if the editor still holds the latest revision. */
export async function saveStyleSprites(
  styleId: string,
  placements: SpritePlacement[],
  expectedRevision: bigint,
): Promise<StyleSpriteBindings> {
  const body = toBinary(
    SaveStyleSpritesRequestSchema,
    create(SaveStyleSpritesRequestSchema, {
      styleId,
      placements,
      expectedRevision,
    }),
  );
  const data = await request(
    `/api/sprites/styles/${encodeURIComponent(styleId)}`,
    "POST",
    undefined,
    body,
  );
  const bindings = fromBinary(SaveStyleSpritesResponseSchema, data).bindings;
  if (!bindings) throw new Error("Sprite 保存响应缺少数据");
  return bindings;
}
