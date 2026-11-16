# Remotion agent tools

`contracts.py` defines typed data, `catalog.py` registers nine callable workspace tools, and `registry.py` derives
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
`contracts.py` so accepted versions and library records stay readable.

`catalog_store.py` stores immutable accepted code in MySQL. `library.search` performs bounded, ranked reads in a
worker thread; reuse means copying source into the workspace. `CatalogStore.solidify` records accepted components
and their uses. There is no local catalog fallback or Agent-facing Preset mutation API.

`registry.get_tool` resolves dotted IDs and underscore wire names only within the caller's tool window.
Outer and Plan may execute their control; Executor may execute the workspace tools and its report control.
`tools.inspect` can describe either control without granting execution rights. Removed tool names are rejected.
Registration declares `starts_generation` so the host needs no parallel list of mutating tools.

Run the registry, workspace, historical conversion and layer tests when changing this boundary. Frozen legacy
fixtures exercise migration without keeping the retired generator executable in production.
