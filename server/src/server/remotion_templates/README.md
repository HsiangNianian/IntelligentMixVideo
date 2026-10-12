# Remotion Preset → Sprite

This module runs the three-layer ReAct workflow. The Outer loop only answers,
asks, delegates or stops through `tools_plan_execute`. A delegation starts the
host's standard two-step plan directly: `preset` readies the Presets and
`sprite` composes and saves the Sprite (a `plan` sent with `delegate` is refused;
only `update_plan` takes one). The Executor loop calls the tools of one step and
ends it with `tools_report_step`; the host checks the step's recorded outputs,
starts the next step and publishes the saved Sprite after the last one. A step
reported `needs_input` goes straight to Outer with its questions, since only
Outer can ask the user. The Plan loop only runs to recover a step that is
blocked or used a batch without new evidence. The final delivery is a saved
Sprite published as an isolated Player preview.

## Tool catalog

Eleven business tools are registered with their full input/output contracts.
`tools.inspect` also describes the host-owned `tools.plan_execute` and
`tools.report_step` controls by dotted ID or wire name, without executing them
or changing role permissions.
Six business tools have implementations in this build:

| Tool | Status |
| --- | --- |
| `preset.create` | implemented — validates the Preset and saves it |
| `sprite.compose` | implemented — deterministic combination of Preset instances; keeps the draft host-side and returns its `draft_id` and a code-free summary |
| `sprite.create` | implemented — takes that `draft_id`; code validation (each distinct Preset source once, then the Sprite), one browser mount that must leave the video visible, and save |
| `tools.inspect` | implemented — returns one tool descriptor by dotted ID or wire name; it reads a contract, it does not search or list tools |
| `image.info` / `image.resize` / `image.crop` | contract only |
| `preset.search` | implemented: plain listing with optional keyword; the agent picks what to recall |
| `preset.modify` | implemented: copies a stored Preset or a task draft (`base_*`, earlier `draft_id`), keeps the copy host-side and returns its `draft_id`; compose it with source `{kind: "draft", draft_id}` |
| `validate.code` / `validate.render` | contract only |

Deferred tools are marked `implemented=False` at registration and are filtered
out of every layer's tool window, so the model is never offered a tool that
cannot run. Calling one directly returns `NOT_IMPLEMENTED` rather than a
fabricated result. `tools.inspect` still describes them and says so in the first
words of the description (`[Not callable in this build ...]`); an unknown name
lists callable tools and contract-only tools apart. The host still performs the code, parameter and browser
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
storage errors only. `preset.create` and `sprite.create` run the isolated validator
before storing: when it cannot start (for example `bwrap` is missing) they return
`VALIDATION_UNAVAILABLE` with the original error and store nothing. `preset.modify`
only checks the contract and never starts the validator.

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
The isolation is Linux-only: `POST /works`, a parameter patch sent to
`POST /works/{id}/messages` and the retry of a parameter job return 503 naming the
unsupported platform or the missing `bwrap`, `prlimit` or `node` before anything is
queued, so on macOS the service starts but cannot generate templates. Questions,
clarification replies, free-text edits and other retries are not refused up front,
because the model may answer without a validator; if it does need one, the tool call
returns `VALIDATION_UNAVAILABLE`.
For local development on macOS only, `IMV_ALLOW_UNSANDBOXED_WORKER=true` runs the
generation workers (`tool-validation-worker.mjs`, `presentation-worker.mjs` and
`worker.mjs`) as plain Node instead, and only `node` is required: generated code then
runs with no isolation and a warning is logged at startup. The switch is off by
default and ignored on every other system, so Linux and deployed servers always use
the sandbox. `overlay-worker.mjs` is not on that list: overlay rendering, including
the recovery of pending overlay tasks after a restart, always needs the Linux
sandbox and returns 503 at acceptance.
Offline tests cover tool registration, deferred-tool behaviour, immutable
storage and source consistency without a model or semantic index.

## Creating a Sprite

A Sprite is composited over the user's own video, so `sprite.create` must leave that video visible. After code
validation it mounts the composed Sprite once in the isolated browser and, on the first, last and three
in-between frames, measures how much of the canvas is nearly opaque (alpha of 242/255 or more, from a screenshot
taken without a page background). When at least 80% of the sampled frames (4 of 5) are opaque on 98% or more of
the canvas the Sprite is refused with `CODE_VALIDATION_FAILED`, the reason names the frames and what to do, and
nothing is saved. One clear frame is not enough, because a cover that fades in or starts late is clear on the
first frame and hides the video afterwards; a transition that covers the screen only for a moment is not
affected. Sampling stops once the verdict is settled. The page is opened at the canvas size (1080x1920), as the real overlay render does, so
`100vw`/`100vh`, `window.innerWidth`/`innerHeight` and media queries see the canvas. The mount is cached under
the key the host's final check reads, so completing the task does not mount the Sprite a second time. A component that throws on a sampled frame, or while its
module loads, fails the `transparency` (with the frame) or `default_render` check with its own error, so
the model gets `CODE_VALIDATION_FAILED` and the cause rather than `VALIDATION_UNAVAILABLE`. Only
generation asks for the measurement; a user's manual parameter edits and already published versions are
never blocked by it.

Contract diagnostics carry the position of a violation (line, column and range in the submitted code).
`exec` and `spawn` are refused only as bare identifiers; as member names, such as `RegExp.prototype.exec`,
they are ordinary methods.

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

Drafts are handles: neither the model nor the snapshot carries Sprite or draft source, so a large
composition is never copied back verbatim. Presets of the same source are bundled once however many
instances use them. For an edit, the accepted base's instances are registered as `base_<instance_id>`
Preset drafts; only the Executor's snapshot shows their source, each distinct source once. The session
snapshot stays within 200 KB: a base too large for it has its largest sources omitted first (`code_omitted`,
`legacy_code_omitted`), while their handles still compose unchanged. The execution
snapshot shows `tool_calls_left_in_batch` and `tool_calls_left_in_task` instead of used/limit pairs.
Every call returns a fresh `draft_id`, so the no-progress guard ignores draft handles and compares the
content `digest` that `preset_modify` and `sprite_compose` return: repeating the same draft is no new evidence.

The host records each step's outputs from tool receipts: the Sprites `sprite_create` saved, and the Preset
sources that `preset_create`, `preset_search {preset_id}` or `preset_modify` made ready. A step with the
`sprite` module cannot be reported `step_done` before a `sprite_create` in that step succeeded, and a
`preset` step needs at least one ready Preset (or, for an edit, the base drafts); the refusal names the tool
to call next. Plans reference `steps.<id>.outputs.presets` or `steps.<id>.outputs.sprite`, and a reference to
an output the earlier step's modules cannot produce is refused when the Plan is built. `update_plan` takes
only the unfinished steps; completed steps are kept by the host and a resent copy is ignored by ID, with
the ignored IDs listed in the receipt (`ignored_step_ids`) so the Plan knows to redo work under a new ID. At the
batch limit the host continues a step that observed something new in that batch and wakes the Plan
otherwise.

Outer has no publish action, so publication is the host's. Besides the last accepted step, a Plan
`complete` (or a text `plan_done`) publishes the latest Sprite this task saved; a completion that names any
other Sprite is refused. When the task budget allows no further batch or control call, the host publishes
the latest task Sprite if there is one, and only otherwise returns `blocked` to Outer.
A handoff counts for the no-progress guard only once it has happened: an automatic advance or a
publication the host refuses is recorded as the return to the Plan, not as the transition it attempted.

Call arguments are parsed by the host. Only surplus `}` or `]` after one complete JSON object are dropped
(and recorded as `tool_arguments_repaired` in the private audit); any other invalid JSON is refused with the
character position and the text around it, and schema errors give the first JSON Pointer as `field` and up
to three located messages. A refused create lists up to five errors with file, line:column, code and the
checked source line (`excerpt`, because the type check runs on a formatted copy whose line numbers can
differ from the submission), and adds fixed host hints in `details.hints` for props/schema mismatches,
disallowed imports or capabilities, the transparency gate and schema/default mismatches. The Executor's
rules include one minimal Preset that passes code validation and the transparency mount.

Text replies remain a fallback. Executor and Plan accept a single JSON object with ordinary prose or a
markdown fence around it, and curly quotes used as JSON delimiters are straightened. They reject arrays,
quoted JSON strings, multiple objects and objects nested inside malformed JSON. Unknown task Sprite IDs in
a Plan result produce feedback without changing the active layer or Plan. When the task has saved no
Sprite at all, publication is refused with that fact and control returns to the Plan.

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
`imv.sprite.v1.PublishedSprite` (a row of the shared MySQL table `remotion_sprites`, `sprite_store.py`, plus
`sprites/<id>/interactive.js` in this module's data folder), so deleting or editing the source work never changes a
published Sprite. The table is created on first use like the template table and sits in the same database as the
templates that reference its IDs, which is what lets a template save check its `remotion` tracks (422 when a
`sprite_id` is not published or the clips' Sprites do not share one width, height and fps, which an overlay render requires). The preview bundle is a local file: a host that did not publish the Sprite lists it
but answers its preview with a 404, while overlay renders only need the stored TSX. Sprites published before this
storage (the SQLite `sprites` table and the `sprites/<id>/` folders it pointed at) are neither read nor migrated and
stay where they are: publish the accepted versions again.
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
failure. Where a Sprite is placed is not stored here: a placement is a `remotion` track of the cloud template
(`sprite_id`, a seconds start, an optional duration), saved with the template in one request, so deleting a
template removes its placements with it and the composition bus finds them with its single template lookup.
There is no separate route for placements; the template save checks that every `sprite_id` is published. The
SQLite `style_sprite_bindings` table and the `GET/POST /api/sprites/styles/{style_id}` routes of earlier versions
are gone: bindings saved there are neither read nor migrated, and an existing table is left in place. The router
lives in `sprite_router.py` on the main app and borrows the Remotion runtime; `/api/sprites/render` is superseded
by the overlay renders below.
`GET /api/sprites/{id}/preview?overlay=true&sync=1` serves the sealed player on a transparent page for the template editor;
a sync-mode bundle has no controls or loop and seeks on `imv-preview-sync {time}` messages, reporting `sync: true` in its
ready message. Publishing rebuilds the bundle from the sealed source in the isolated worker when the sealed one lacks
the marker (and refreshes an existing publication on re-save); a failed rebuild still saves the asset, just without overlay preview.
A refresh stages the new bundle and records its hash before replacing the copy, so a failed update keeps the
previous copy working and publishing again retries it. Identical publish requests (same version and field
choice) take turns, so a double click rebuilds once.

## Overlay renders for the bus

`POST /api/v1/overlay-renders` (`overlay_router.py`) renders **the Remotion clips of a request**
into one transparent VP9 alpha WebM, uploads it to ZOS (`imv/overlay_render/{taskId}.webm`, public-read) and
returns/notifies the public URL. Body (camelCase): `placements` (at most 100, bottom layer first, each
`{spriteId, start, duration?}`: `start` is a finite number of seconds from 0 and `duration` a number above 0, both
strict like the top-level `duration`; the bus reads them from the template's `remotion` tracks, so a template is
looked up once and this module never reads the IMS template library), optional `audioUrl`, `duration` (seconds),
`callbackUrl`. `GET /api/v1/overlay-renders/{taskId}` returns `{taskId, status, result:{videoUrl}, error}`
(`queued/rendering/uploading/succeeded/failed`); the callback body is `{taskId, status: succeed|failed,
videoUrl, errorMessage}`, retried after 5/15/45 s; each attempt has a 30 s overall deadline, and a non-2xx answer, an
error (including an address httpx cannot parse) or giving up after the fourth attempt is logged. Once delivered or
given up the row is marked `notified` (a restart re-sends only unnotified terminal tasks) and the result is never
touched. A failure publishes only a stable `error.code` and a fixed
message (also the callback's `errorMessage`); the renderer's output and other root causes go to the server log.

- **Length**: TTS `audioUrl` (full ffprobe duration) wins; otherwise `duration` (a JSON number above 0, strict like
  the composition API's `videoDuration`: booleans and numeric strings are a 422); otherwise the latest clip end.
  Longer than `IMV_OVERLAY_MAX_SECONDS` (120) is a 422 at acceptance when the length is already known (`duration`
  or the clips' end); an `audioUrl` is only probed while rendering, so an over-long one fails the task with
  `duration_too_long`.
- **Acceptance**: also a 503 when ZOS, ffprobe, bubblewrap, prlimit, Node, the browser or the managed fonts are
  missing (the isolated renderer is Linux-only), instead of accepting tasks that can only fail later. An empty
  `placements` (`no_placements`) or a `spriteId` that is not published (`sprite_missing`) is a 422.
- **Layout**: every asset must share width/height/fps. Clips are stacked in request order on one canvas (the first
  is the bottom layer); each plays for its `duration` (or its own `preview_frames / fps`), which its Sprite reads as `durationInFrames` just like in
  its accepted preview, keeps that full length when the total cuts it (so its internal animation ranges are
  unchanged), and is dropped when it starts later. Seconds become frames with half a frame rounding up, like the
  template timing, and a clip is at most `IMV_OVERLAY_MAX_SECONDS` long. Remotion clamps a Sequence to its
  composition, so the worker declares the composition up to the latest clip end and renders only the first `total`
  frames of it.
- **Output**: no audio; the bus/IMS keeps its own audio track and overlays the WebM. The host re-probes the file
  (VP9, size, fps, frame count, alpha tag) before uploading.
- **Execution**: `overlay_render.py` plans, `remotion/overlay-worker.mjs` renders in the bwrap sandbox
  (`IMV_OVERLAY_RENDER_TIMEOUT_SECONDS`, 3600), `overlay_service.py` owns the SQLite `overlay_renders` task table,
  one render at a time (the slot covers only the render: the remote audio probe before it and the ZOS upload after it
  run outside, so a slow network never delays queued renders), recovery of unfinished tasks and pending notifications
  when the runtime is first built
  (`routes.runtime_for`, oldest first; a damaged row is logged and skipped, and the overlay routes retry if the table
  could not be read), and ZOS upload via `video_composition.zos.upload_public`. The placements are stored with the
  task, so a recovered task renders exactly what was accepted and only re-reads the published Sprites. State writes retry a locked database
  three times; if it stays unwritable the row keeps its previous state (never rewritten as a failure) and the next
  start re-runs it. Sealed sources are not re-policy-checked in this worker.
- **Cost**: a 1080×1920 frame takes about 0.4–1 s on one Chrome tab (software GL), so a 120 s overlay at 30 FPS needs
  roughly 25–60 minutes: size `IMV_OVERLAY_RENDER_TIMEOUT_SECONDS` (at most 3600) for `IMV_OVERLAY_MAX_SECONDS` × FPS
  × seconds per frame, or lower the length. The single VP9 encoder process and Chrome's GL process each need about
  0.9 CPU seconds per frame, so the sandbox's per-process CPU cap is the render timeout times the host's cores
  (never below the 300 s of the short validation workers) instead of a fixed 300 s.

## Code diagnostics

`GET /api/templates/versions/{id}/diagnostics` re-runs the isolated TypeScript
language service over an accepted version's sealed source and returns its
contract and LSP diagnostics. It re-checks the sealed `accepted/` artifacts
first, so tampered bytes return 404 rather than a stale conclusion, and an
unavailable sandbox returns 503 instead of reporting "no diagnostics" for code
that was never checked. The route is read-only: it never queues work, publishes
a version or adds a diagnostics table.
