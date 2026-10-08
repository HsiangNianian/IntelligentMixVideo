# Remotion Preset → Sprite

This module runs the three-layer ReAct workflow: the Outer task loop plans, the
Plan loop sequences steps, and the Executor loop calls tools for one step. The
final delivery is a saved Sprite published as an isolated Player preview.

## Tool catalog

All eleven tools in `docs/remotion-agent-tool-contracts.md` are registered with
their full input/output contracts, so `tools.inspect` can describe any of them.
Only four have implementations in this build:

| Tool | Status |
| --- | --- |
| `preset.create` | implemented — validates the Preset and saves it |
| `sprite.compose` | implemented — deterministic combination of Preset instances |
| `sprite.create` | implemented — consistency check, code validation and save |
| `tools.inspect` | implemented — returns one exact tool descriptor |
| `image.info` / `image.resize` / `image.crop` | contract only |
| `preset.search` / `preset.modify` | contract only |
| `validate.code` / `validate.render` | contract only |

Deferred tools are marked `implemented=False` at registration and are filtered
out of every layer's tool window, so the model is never offered a tool that
cannot run. Calling one directly returns `NOT_IMPLEMENTED` rather than a
fabricated result. The host still performs the code, parameter and browser
checks needed to build a preview; those are not model-facing tools.

Image tools read the reference image the user already uploaded and the server
registered as a local asset. They take that asset reference, not a URL, and
never upload anything.

## Preset storage

`preset.create` writes through `tools/catalog_store.py`, which targets MySQL via
the shared `server.database` settings (`DB_*`). When the database is unreachable
it falls back to the task-local catalog under the module data directory, and
`ToolSession.snapshot()` reports the backend that was actually used. Both paths
append immutable records only; there is no update or delete.

## Preview

The preview is built from the saved Sprite's exact code, schema and defaults.
The main canvas is 1080×1920 at 30 FPS and the last instance's exclusive end
determines the duration.

For Linux renderer validation, install the locked Bun dependencies in
`server/src/server/remotion`, then run:

```sh
IMV_TEST_RENDERER=1 uv run --locked --project server pytest server/tests/test_remotion_templates.py -q
```

The browser path requires Chromium, Noto CJK fonts, bubblewrap and util-linux.
Offline tests cover tool registration, deferred-tool behaviour, immutable
storage and source consistency without a model or semantic index.
