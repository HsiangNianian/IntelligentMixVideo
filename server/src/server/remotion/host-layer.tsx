/**
 * Host transform of one placed Sprite, shared by the interactive preview, publish-time boundary samples and the
 * overlay render: it moves, scales, rotates and fades the whole Sprite around the centre of its content box.
 * Generated code never sees it, so a template changes position and opacity without touching the sealed source.
 */
import React from "react";
import { AbsoluteFill } from "remotion";

/** Content area on the canvas in canvas pixels; its centre is the default position and the pivot. */
export type HostBox = { x: number; y: number; width: number; height: number };
/** Optional placement values; a missing field keeps the sample's own position, size, angle or opacity. */
export type HostTransform = { x?: number; y?: number; scale?: number; rotation?: number; opacity?: number };

/**
 * Composed Sprites put every instance in an absolutely positioned box of its layout size. That box is only a region
 * for placing the instance and must not cut it: a larger font, a longer keyword or a taller underline would be
 * cropped at the box edge while the canvas still has room. Newly composed Sprites no longer clip it; Sprites
 * sealed earlier did (`overflow: hidden`), so the host lifts it for them too. Only the canvas bounds the picture.
 * `routes.player_page` carries the same rule for bundles built before this was added.
 */
const INSTANCE_BOX_CSS = "div:has(> [data-imv-instance]){overflow:visible !important}";

/** Read a finite number or fall back, so a partial transform never produces NaN styles. */
function finite(value: number | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/**
 * Wrap a Sprite in the same two layers whatever the values, so editing a transform never remounts the Sprite
 * and restarts its state. The outer layer fades the whole Sprite; the inner layer moves the content centre to
 * (x, y) and rotates and scales around it.
 */
export function HostLayer({ box, transform, children }: { box: HostBox; transform?: HostTransform; children: React.ReactNode }) {
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  const x = finite(transform?.x, cx);
  const y = finite(transform?.y, cy);
  const scale = finite(transform?.scale, 1);
  const rotation = finite(transform?.rotation, 0);
  const opacity = finite(transform?.opacity, 1);
  return (
    <AbsoluteFill data-imv-host-layer style={{ opacity }}>
      <AbsoluteFill
        style={{
          transformOrigin: `${cx}px ${cy}px`,
          transform: `translate(${x - cx}px, ${y - cy}px) rotate(${rotation}deg) scale(${scale})`,
        }}
      >
        {children}
      </AbsoluteFill>
      {/* After the transformed layer: the text measurements read the host layer's first child as the content. */}
      <style>{INSTANCE_BOX_CSS}</style>
    </AbsoluteFill>
  );
}
