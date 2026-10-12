# Remotion agent tools

`contracts.py` declares the complete data contract for all eleven tools in
`docs/remotion-agent-tool-contracts.md`; `catalog.py` registers each one with the
decorator in `registry.py`, which derives strict provider schemas from the typed
handlers. Tools whose implementation is deferred are registered with
`implemented=False`, and `available()` filters them out of every layer's tool
window so the model only sees tools that can actually run.

`ToolSession` owns immutable task-local records. `catalog_store.py` persists
Presets to MySQL with a local JSON fallback, and `compose.py` generates the
deterministic single-file Sprite source. The host builds the preview after the
saved Sprite passes its checks.
Local catalog transactions use a cross-process file lock and unique atomic
replacement files; database connection/query failures preserve local visibility.
Catalog I/O runs outside the event loop and file-lock acquisition waits at most
10 seconds. `preset.modify` validates the merged component's data contract,
including schema/default consistency and local-only references, before keeping
the draft in the task session and returning its `draft_id`; it neither compiles the
code nor writes the draft to the catalog. `sprite.compose` keeps its draft the same
way and `sprite.create` takes only that `draft_id`.

`sprite.compose` (contract v2) also takes the Sprite `kind` and `parameters`
bindings. A binding lifts declared top-level Preset props into one required
top-level Sprite parameter (`compose.py` checks the targets share one schema and
one value, removes them from the instances' nested schemas and defaults, and
`compose.mjs` passes the top-level value to every target). Bound parameters need
bounded schemas (`categories.parameter_problems`). `sprite.create` refuses a
Sprite without its kind's required parameters (`categories.REQUIRED`) with
`CATEGORY_PARAMETERS_MISSING` before the browser mount, so the Executor fixes the
bindings in its loop.

`registry.get_tool` is the single name resolver: it accepts the dotted ID or the
provider wire name (`wire_name`) and rejects everything else with the tool
list and closest names from the supplied window. Agent dispatch and `resolve`
pass only the current layer's permitted tools; inspection passes all registered
contracts plus host Plan control without granting execution rights. Because
inspection also holds contract-only tools, its unknown-name error lists callable
tools and contract-only tools apart, and a contract-only descriptor begins
`[Not callable in this build ...]`.
Registration also declares `starts_generation`, so the
host never keeps its own tool-name sets. `tests/test_remotion_tool_registry.py`
fails when a registered tool is not resolvable, inspectable and correctly
exposed, which is the check to run after adding a tool.

Inspection includes `contract_version`: 1 by default, 2 for the implemented
`preset.search` listing/ID-lookup contract. Search returns bounded summaries and
`has_more`; storage and response-size failures are declared in its descriptor.
