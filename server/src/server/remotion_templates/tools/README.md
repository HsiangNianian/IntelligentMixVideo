# Remotion agent tools

`contracts.py` defines typed data, `catalog.py` registers ten callable workspace tools, and `registry.py` derives
strict provider schemas from their handlers. The complete public API is described in
[`docs/remotion-agent-tool-contracts.md`](../../../../../docs/remotion-agent-tool-contracts.md).

`ToolSession` owns `Effect.tsx`, `effect.json`, validation reports and immutable task candidates. `workspace.py`
creates starters, restores accepted versions, assembles source and derives preview observations. `EffectTools`
provides reads, exact edits, previews, checks and saves; code runs only through the isolated validator.
`compose.py` bundles the component and generates standard configuration fields. There is no role composer or
built-in effect recipe implementation.

Older complete components open with their source, full parameter contract and current defaults in the same
workspace. Conversion never writes to history. The manifest's optional `contract` preserves nested parameters
and content bounds, and follows the same save gate as a new effect. Historical serialization models remain in
`contracts.py` so accepted historical versions stay readable.

`../source_library.py` stores immutable accepted components in the shared `remotion_effect_sources` table.
`library.search` reads bounded summaries and `library.read` pages through complete files in worker threads.
Successful Agent publication archives both workspace files atomically, deduplicated by content hash; manual
parameter revisions and failed drafts are excluded. Reuse copies files into the workspace and revalidates them.
The library survives work deletion and is separate from published `remotion_sprites`. Old Preset tables and
usage ranking have no production callers; there is no local catalog fallback or Agent-facing mutation API.

`registry.get_tool` resolves dotted IDs and underscore wire names only within the caller's tool window.
Outer and Plan may execute their control; Executor may execute the workspace tools and its report control.
`tools.inspect` can describe either control without granting execution rights. Removed tool names are rejected.
Registration declares `starts_generation` so the host needs no parallel list of mutating tools.

Run the registry, workspace, historical conversion and layer tests when changing this boundary. Frozen legacy
fixtures exercise migration without keeping the retired generator executable in production.
