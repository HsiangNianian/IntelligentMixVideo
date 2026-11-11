/** Draw deterministic tutorial annotations as SVG: gently curved frames, curved arrows, and large numbered badges. No raster editing or AI is involved. */

/** Keep a coordinate inside the visible image, including its stroke or badge radius. */
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

/** A badge must leave the highlighted control readable, including at viewport edges. */
function touchesFrame(x, y, radius, frame) {
  return Math.hypot(x - clamp(x, frame.x, frame.x + frame.width), y - clamp(y, frame.y, frame.y + frame.height)) < radius + 5;
}

/** Use authored placement hints, with bounded alternatives for cramped controls; fail rather than obscure a marked control. */
export function layoutCallouts(items, viewport) {
  const frames = items.map(({ box, padding = 6, paddingY = padding }) => {
    // Dense rows need less vertical clearance while text still needs space beside rounded corners.
    const x = Math.max(4, box.x - padding), y = Math.max(4, box.y - paddingY);
    return { x, y, width: Math.min(viewport.width - 4, box.x + box.width + padding) - x, height: Math.min(viewport.height - 4, box.y + box.height + paddingY) - y };
  });
  const placed = [];
  for (const [index, item] of items.entries()) {
    const frame = frames[index], radius = (item.size ?? 48) / 2;
    if (frame.width <= 0 || frame.height <= 0) throw new Error(`Invalid callout region ${index + 1}`);
    const preferred = item.side ?? (frame.x > viewport.width / 2 ? "left" : "right");
    const sides = [...new Set([preferred, "left", "right", "top", "bottom"])];
    let chosen;
    for (const side of sides) {
      for (const shift of [...new Set([item.shift ?? -20, 0, -40, 40, -70, 70])]) {
        for (const gap of [...new Set([item.gap ?? 76, 58, 100])]) {
          const vertical = side === "left" || side === "right";
          const along = clamp(item.align ?? 0.5, 0.05, 0.95);
          const end = {
            x: vertical ? frame.x + (side === "right" ? frame.width + 4 : -4) : frame.x + frame.width * along,
            y: vertical ? frame.y + frame.height * along : frame.y + (side === "bottom" ? frame.height + 4 : -4),
          };
          const x = clamp(end.x + (vertical ? (side === "right" ? gap : -gap) : shift), radius + 4, viewport.width - radius - 4);
          const y = clamp(end.y + (vertical ? shift : (side === "bottom" ? gap : -gap)), radius + 4, viewport.height - radius - 4);
          if (frames.some(other => touchesFrame(x, y, radius, other))) continue;
          if (placed.some(other => Math.hypot(x - other.x, y - other.y) < radius + other.radius + 10)) continue;
          chosen = { frame, x, y, radius, end, side };
          break;
        }
        if (chosen) break;
      }
      if (chosen) break;
    }
    if (!chosen) throw new Error(`No clear space for callout ${index + 1}; adjust its region or placement`);
    placed.push(chosen);
  }
  return placed;
}

/** Rounded contours use small, fixed bends rather than a perfectly rectangular browser border. */
function outline({ x, y, width: w, height: h }, index) {
  const r = Math.min(22, w / 2, h / 2), bend = 1.1 + (index % 3) * 0.35;
  return `M ${x + r} ${y}
    C ${x + w * .34} ${y - bend} ${x + w * .68} ${y + bend} ${x + w - r} ${y}
    Q ${x + w} ${y} ${x + w} ${y + r}
    C ${x + w + bend} ${y + h * .34} ${x + w - bend} ${y + h * .68} ${x + w} ${y + h - r}
    Q ${x + w} ${y + h} ${x + w - r} ${y + h}
    C ${x + w * .68} ${y + h + bend} ${x + w * .34} ${y + h - bend} ${x + r} ${y + h}
    Q ${x} ${y + h} ${x} ${y + h - r}
    C ${x - bend} ${y + h * .68} ${x + bend} ${y + h * .34} ${x} ${y + r}
    Q ${x} ${y} ${x + r} ${y} Z`;
}

/** A quadratic leader ends with a round, open arrowhead, in the same pen stroke as the frame. */
function arrow({ x, y, radius, end, side }) {
  const dx = end.x - x, dy = end.y - y, length = Math.hypot(dx, dy);
  const bend = Math.min(22, length * .18) * (side === "left" || side === "top" ? 1 : -1);
  const control = { x: (x + end.x) / 2 - dy / length * bend, y: (y + end.y) / 2 + dx / length * bend };
  const startLength = Math.hypot(control.x - x, control.y - y);
  const start = { x: x + (control.x - x) / startLength * (radius + 5), y: y + (control.y - y) / startLength * (radius + 5) };
  const angle = Math.atan2(end.y - control.y, end.x - control.x), head = 11;
  const wing = sign => `${end.x - Math.cos(angle + sign * .6) * head} ${end.y - Math.sin(angle + sign * .6) * head}`;
  return `M ${start.x} ${start.y} Q ${control.x} ${control.y} ${end.x} ${end.y} M ${wing(-1)} L ${end.x} ${end.y} L ${wing(1)}`;
}

/** SVG is composed only from numeric layout data; the browser renders it over the live client before capture. */
export function renderCallouts(items, viewport) {
  const placed = layoutCallouts(items, viewport);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${viewport.width}" height="${viewport.height}" viewBox="0 0 ${viewport.width} ${viewport.height}">
    <defs><radialGradient id="tutorial-badge" cx="30%" cy="22%" r="90%"><stop offset="0" stop-color="#ff7167"/><stop offset=".48" stop-color="#ff4b4b"/><stop offset="1" stop-color="#ed383b"/></radialGradient></defs>
    <g fill="none" stroke="#ff4b4b" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round">
      ${placed.map((item, index) => `<path d="${outline(item.frame, index)}"/><path d="${arrow(item)}"/>`).join("")}
    </g>
    ${placed.map(({ x, y, radius }, index) => `<g><circle cx="${x}" cy="${y}" r="${radius}" fill="url(#tutorial-badge)" stroke="#ffffff" stroke-opacity=".8" stroke-width="1"/><text x="${x}" y="${y + 1}" text-anchor="middle" dominant-baseline="central" fill="white" font-family="Arial, sans-serif" font-size="${radius * 1.35}" font-weight="700">${index + 1}</text></g>`).join("")}
  </svg>`;
}
