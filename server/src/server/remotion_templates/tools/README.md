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
