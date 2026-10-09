# Remotion Preset → Sprite

This module runs the three-layer ReAct workflow: the Outer task loop plans, the
Plan loop sequences steps, and the Executor loop calls tools for one step. The
final delivery is a saved Sprite published as an isolated Player preview.

## Tool catalog

Eleven business tools are registered with their full input/output contracts.
`tools.inspect` also describes the host-owned `tools.plan_execute` control by
dotted ID or wire name, without executing it or changing role permissions.
Only four business tools have implementations in this build:

| Tool | Status |
| --- | --- |
| `preset.create` | implemented — validates the Preset and saves it |
| `sprite.compose` | implemented — deterministic combination of Preset instances |
| `sprite.create` | implemented — consistency check, code validation and save |
| `tools.inspect` | implemented — returns one tool descriptor by dotted ID or wire name |
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

## Loop rounds

Every Outer → Plan → Executor turn appends one public round record through the
`job.round` delta event on the same work stream as the phase timeline. A round
carries the layer, the turn and the tools the host handled in that turn — the
canonical dotted tool ID, whether it passed, and on failure a whitelisted error
code plus a fixed host-authored message. Tool arguments and result payloads
stay in the private audit, plan step goals and model prose
are never published, a turn that only replied records an empty call list, and a
terminal job accepts no further rounds. Unknown tool names become `unknown`;
model protocol failures are explicitly marked. Snapshots carry all rounds, while
`job.updated` omits that history and clients retain already received deltas.

## Code diagnostics

`GET /api/templates/versions/{id}/diagnostics` re-runs the isolated TypeScript
language service over an accepted version's sealed source and returns its
contract and LSP diagnostics. It re-checks the sealed `accepted/` artifacts
first, so tampered bytes return 404 rather than a stale conclusion, and an
unavailable sandbox returns 503 instead of reporting "no diagnostics" for code
that was never checked. The route is read-only: it never queues work, publishes
a version or adds a diagnostics table.
