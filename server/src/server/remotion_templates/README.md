# Remotion Preset → Sprite

This module runs the three-layer ReAct workflow: the Outer task loop plans, the
Plan loop sequences steps, and the Executor loop calls tools for one step. The
final delivery is a saved Sprite published as an isolated Player preview.

## Tool catalog

Eleven business tools are registered with their full input/output contracts.
`tools.inspect` also describes the host-owned `tools.plan_execute` control by
dotted ID or wire name, without executing it or changing role permissions.
Six business tools have implementations in this build:

| Tool | Status |
| --- | --- |
| `preset.create` | implemented — validates the Preset and saves it |
| `sprite.compose` | implemented — deterministic combination of Preset instances |
| `sprite.create` | implemented — consistency check, code validation and save |
| `tools.inspect` | implemented — returns one tool descriptor by dotted ID or wire name |
| `image.info` / `image.resize` / `image.crop` | contract only |
| `preset.search` | implemented: plain listing with optional keyword; the agent picks what to recall |
| `preset.modify` | implemented: returns an edited draft copy; save it with `preset.create` |
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
Connection and query failures also fall back to local records. Local appends
hold a cross-process file lock over the entire read/modify/write transaction
and replace the catalog from a unique, flushed temporary file.
Catalog reads and writes run in worker threads, so database and lock waits do
not block the event loop or delay cancellation. File-lock waits expire after
10 seconds on every platform. An atomic write already in progress may finish
after task cancellation; it does not publish a task version.

`preset.search` merges database and local records, sorts by creation time newest
first, and then applies `limit`. UUID ordering and storage backend do not affect
which recent Presets are returned.
Legacy timestamps without a timezone are interpreted as UTC; invalid timestamps
sort last without hiding the record. `tools.inspect` identifies this search
contract as version 2, replacing the previously unimplemented `matches` shape.
`limit` accepts 1–100, and serialized search content is capped at 40,000 UTF-8
bytes including JSON string escaping. `has_more` marks omitted summaries;
oversized individual summaries or full records return `RESOURCE_LIMIT_EXCEEDED`
without truncating source. Search and modify declare `PRESET_STORE_FAILED` for
storage errors.

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

`IMV_ENFORCE_NO_PROGRESS` defaults to true and `IMV_MAX_NO_PROGRESS_TURNS` to 6.
New tool observations, new handoffs and advancing completed steps reset the
counter. Repeated handoffs at the same step count as stalls, including valid
`delegate`/`blocked` and `continue`/`blocked` loops. Changing summaries, reasons,
plan revision numbers or batch counters does not count as progress.
The threshold counts model rounds, not individual tool calls. A round counts
at most once, and any new evidence in that round resets the counter even if
later calls repeat earlier results.
Only a successful generation-starting tool sets `generation_started`. A failed
first attempt may still be followed by an explanatory answer; failure after a
successful generation attempt does not remove the completion requirement.

Executor and Plan accept a single JSON object with ordinary prose or a markdown
fence around it. They reject arrays, quoted JSON strings, multiple objects and
objects nested inside malformed JSON. Unknown task Sprite IDs produce feedback
without changing the active layer or Plan, so the next round can correct them.

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

## Sprite assets

`POST /api/sprites/publish` copies an accepted version into an immutable
`imv.sprite.v1.PublishedSprite` (SQLite `sprites` table plus `sprites/<id>/interactive.js`),
so deleting or editing the source work never changes a published Sprite.
Publishing re-verifies the sealed `accepted/` artifacts (409 when changed), requires the
declared kind to match a version-fixed kind (composition versions choose it), and for text
Sprites requires `text_prop` to be a string parameter. Parameters are addressed by dot path
(`title_main.title`), so composed Sprites with nested objects work. Scalar leaves at any depth become
`VISIBLE_EDITABLE` controls; text and keyword fields stay with the composition bus. The same
source and field choice returns the original Sprite. `animation_frames` and `static_frame` stay 0
because composition versions carry no such evidence, and `preview_url` serves the sealed
interactive player page rather than an MP4.

`GET /api/sprites` lists summaries (no TSX). A record that cannot be parsed or fails its hash check is
logged and left out, so one damaged row never hides the library; reading it by ID still reports the
failure. `GET/POST /api/sprites/styles/{style_id}` read and replace the Remotion clips placed under one
style ID in this module's own SQLite database (`style_sprite_bindings`) with an `expected_revision` lock (409);
the check and the write share one immediate transaction. The ID only associates clips with a cloud template:
it is never looked up in the IMS template library, so no IMS template has to exist, saving or deleting a
template does not touch the clips (clips of a deleted template stay unread), and IMS rules and tables never
enter this module. Saving validates only what Remotion owns: referenced assets exist, clips are fixed
(`start_mode` seconds, a finite start of at least 0, an optional positive duration, contiguous `order`, unique
IDs, at most 100); object targets and style overrides are refused. The router lives in `sprite_router.py` on the
main app and borrows the Remotion runtime; `/api/sprites/render` is not implemented.
`GET /api/sprites/{id}/preview?overlay=true&sync=1` serves the sealed player on a transparent page for the template editor;
a sync-mode bundle has no controls or loop and seeks on `imv-preview-sync {time}` messages, reporting `sync: true` in its
ready message. Publishing rebuilds the bundle from the sealed source in the isolated worker when the sealed one lacks
the marker (and refreshes an existing publication on re-save); a failed rebuild still saves the asset, just without overlay preview.
A refresh stages the new bundle and records its hash before replacing the copy, so a failed update keeps the
previous copy working and publishing again retries it. Identical publish requests (same version and field
choice) take turns, so a double click rebuilds once.

## Code diagnostics

`GET /api/templates/versions/{id}/diagnostics` re-runs the isolated TypeScript
language service over an accepted version's sealed source and returns its
contract and LSP diagnostics. It re-checks the sealed `accepted/` artifacts
first, so tampered bytes return 404 rather than a stale conclusion, and an
unavailable sandbox returns 503 instead of reporting "no diagnostics" for code
that was never checked. The route is read-only: it never queues work, publishes
a version or adds a diagnostics table.
