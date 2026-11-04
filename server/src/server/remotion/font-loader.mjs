/** Browser-only managed font loading shared by render entries and previews; every fallback is bounded. */

/** Try each weight's primary face, then bundled SC, then local Chinese fonts; never leave rendering blocked. */
export async function loadManagedFonts(sources, fallback) {
  document.documentElement.lang = "zh-CN";
  document.documentElement.style.fontFamily = '"Noto Sans CJK SC", "Noto Sans SC", sans-serif';
  const selections = {};
  await Promise.all([400, 700].map(async (weight) => {
    for (const [selection, source] of [["configured", sources[weight]], ["bundled", fallback]]) {
      let timer;
      try {
        const face = new FontFace("Noto Sans CJK SC", typeof source === "string" ? `url(${JSON.stringify(source)})` : source, { weight: String(weight) });
        const loaded = await Promise.race([
          face.load(),
          new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Managed font load timed out")), 10000); }),
        ]);
        document.fonts.add(loaded);
        selections[weight] = selection;
        if (selection === "bundled") console.info(`[managed-font-load] ${weight} bundled`);
        return;
      } catch {
        console.warn(`[fonts] Weight ${weight}: ${selection} font unavailable`);
      } finally {
        clearTimeout(timer);
      }
    }
    selections[weight] = "local";
    console.info(`[managed-font-load] ${weight} local`);
    console.warn(`[fonts] Weight ${weight}: using local Chinese font fallback`);
  }));
  return selections;
}
