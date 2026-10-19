/**
 * Geometry of an in-place keyword: a body text effect marks its keyword hit with `data-imv-keyword`, the host hides
 * that element (keeping its space) and draws a separate keyword effect exactly over it. This classic script defines
 * `globalThis.imvKeywordAnchor`; `overlay-worker.mjs` imports it into the render bundle and `routes.player_page`
 * inlines it into the template preview page, so the render and the editor measure the same way.
 *
 * `spots` reads where the body shows its keyword (following the body's own animation and placement), `restText`
 * where the keyword effect lays out its text without the transforms it animates (so a keyword that flies or shakes
 * keeps that motion around the spot). Both return canvas pixels; the caller maps the rest box onto the spot.
 */
(() => {
  /** Map a viewport rectangle to the pixels of `canvas`, an element laid out at the composition's size. */
  function toCanvas(rect, canvas) {
    const frame = canvas.getBoundingClientRect();
    const ratio = frame.width / canvas.offsetWidth || 1;
    return { x: (rect.left - frame.left) / ratio, y: (rect.top - frame.top) / ratio, width: rect.width / ratio, height: rect.height / ratio };
  }

  /**
   * The keyword hits marked under `root`: their text (without whitespace), the box of their glyphs in `canvas`
   * pixels and the opacity they are painted with (the product of their own and their ancestors' opacity up to and
   * including `root`), so the keyword drawn over a spot fades in and out with the body. Empty boxes are skipped.
   */
  function spots(root, canvas) {
    return [...root.querySelectorAll("[data-imv-keyword]")].flatMap((element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      const rect = range.getBoundingClientRect();
      if (!rect.width || !rect.height) return [];
      let opacity = 1;
      for (let item = element; item && item !== root.parentElement; item = item.parentElement) opacity *= Number(getComputedStyle(item).opacity);
      return [{ text: (element.textContent ?? "").replace(/\s+/g, ""), ...toCanvas(rect, canvas), opacity }];
    });
  }

  /**
   * The box of all text under `layer` in `layer` pixels, as laid out before the transforms the effect animates:
   * each text node's glyph box is mapped from its element's painted box to the element's layout box (offsets up to
   * `layer`), which undoes the element's and its ancestors' transforms. Text in elements without a layout box (SVG)
   * falls back to the painted box. Returns null when no text is shown.
   */
  function restText(layer) {
    let box = null;
    const walker = document.createTreeWalker(layer, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const element = node.parentElement;
      if (!element || !node.nodeValue.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const glyphs = range.getBoundingClientRect();
      const painted = element.getBoundingClientRect();
      if (!glyphs.width || !glyphs.height) continue;
      let rect;
      if (element.offsetWidth && element.offsetHeight && painted.width && painted.height) {
        let [left, top] = [0, 0];
        for (let item = element; item && item !== layer; item = item.offsetParent) [left, top] = [left + item.offsetLeft, top + item.offsetTop];
        const [sx, sy] = [element.offsetWidth / painted.width, element.offsetHeight / painted.height];
        rect = { x: left + (glyphs.left - painted.left) * sx, y: top + (glyphs.top - painted.top) * sy, width: glyphs.width * sx, height: glyphs.height * sy };
      } else rect = toCanvas(glyphs, layer);
      box = box ? {
        x: Math.min(box.x, rect.x), y: Math.min(box.y, rect.y),
        width: Math.max(box.x + box.width, rect.x + rect.width) - Math.min(box.x, rect.x),
        height: Math.max(box.y + box.height, rect.y + rect.height) - Math.min(box.y, rect.y),
      } : rect;
    }
    return box;
  }

  /**
   * The host transform `{x, y, scale, opacity}` (see `host-layer.tsx`: the content-box centre `box` moves to (x, y)
   * and the content scales around it) that puts the rest text box `rest` of a keyword effect on `spot` at the
   * spot's height and opacity. Null when either is missing.
   */
  function placement(spot, rest, box) {
    if (!spot || !rest || !rest.height) return null;
    const scale = spot.height / rest.height;
    const [cx, cy] = [box.x + box.width / 2, box.y + box.height / 2];
    return {
      x: spot.x + spot.width / 2 - scale * (rest.x + rest.width / 2 - cx),
      y: spot.y + spot.height / 2 - scale * (rest.y + rest.height / 2 - cy),
      scale, opacity: spot.opacity,
    };
  }

  globalThis.imvKeywordAnchor = { spots, restText, placement };
})();
