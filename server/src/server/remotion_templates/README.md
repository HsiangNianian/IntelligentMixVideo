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

Image tools read the reference images the user already uploaded and the server
registered as reference assets (objects under `imv/remotion/assets/`, read through the verified cache): the work's original images and, for a task started
by a later chat message (`POST /works/{id}/messages` with `images`, up to four),
that message's images. `user_intent.reference_images` lists them in the order the model
receives them, each with its `source` (`work_original`, `message` or
`clarification_answer` plus the answered `clarification` index) and a host-written
`meaning`. They take that asset reference, not a URL, and
never upload anything.

## Storage

The backend keeps no SQLite file. Works, jobs, accepted versions, the model's sliding window, public events and
overlay render tasks are tables in the shared MySQL database (`tables.py`, created on first use next to
`remotion_sprites`). The backend runs as a single instance: one in-process lock serializes write transactions,
startup interrupts jobs a previous process left running and finishes interrupted deletions.

Every work belongs to the client device that created it. JSON routes of a work require the `X-IMV-Device`
header (a UUID the client keeps in its local session database) and treat another device's work as missing;
browser-embedded reads (preview page, fonts, reference image, artifact download) are addressed by unguessable IDs.
Chat history lives on the client: the server buffers public messages and events only until the client stores them
locally and acknowledges the last event ID with `POST /works/{id}/ack`. The open work streams its events over SSE
(`GET /works/{id}/stream`); works the client is not showing catch up through `GET /works/{id}/events?after=<id>`,
one page per call in the same shape, until an empty page. There is no server-side work list: the sidebar reads the
client's local store. Acknowledged events are deleted; once a work
has no undelivered events, the progress, rounds and messages of its finished jobs go too, and only the latest job row
stays because retrying it or answering its questions needs its input.

Sealed version files, reference images and Sprite preview bundles are objects under `imv/remotion/`
(`object_store.py`): public-read in ZOS once `ZOS_BUCKET` is set, otherwise files under `IMV_DATA_DIR/objects`.
The backend reads them through a verified cache in `IMV_DATA_DIR/cache` (SHA-256 against the MySQL record), one file
at a time (deciding whether a parameter patch is instant reads only `interactive.js`). Object uploads, downloads and
deletes run in worker threads and never under the write lock: a version is uploaded under its new ID before its record
commits and removed again if the commit fails; deletion and the upload sweep remove records and objects in separate
steps. A ZOS failure other than a missing object is an `ObjectStoreError` (an `OSError`): uploads, reference images,
downloads, diagnostics and work deletion answer a retryable 503 instead of a 500 or a misleading 422. Job
scratch is removed when the job ends and the private `audit/<job_id>.jsonl` log outlives it. An hourly sweep applies
three retention periods: undelivered events (`IMV_UNDELIVERED_EVENT_DAYS`, 7), audit logs
(`IMV_AUDIT_RETENTION_DAYS`, 14) and the works of devices inactive for `IMV_DEVICE_RETENTION_DAYS` (90). The same
sweep retries every deletion whose cleanup failed (an expired device's work, or a user DELETE answered 503 and never
repeated); each step and each work is isolated, so one storage failure only postpones itself to the next hour.
Published Sprites survive all of these.

## Preset storage

`preset.create` writes through `tools/catalog_store.py` to the MySQL table `remotion_presets`, shared by every
device. A database failure surfaces as `PRESET_STORE_FAILED`; nothing is written to a local fallback. Records are
immutable; there is no update or delete. Catalog reads and writes run in worker threads, so database waits do not
block the event loop or delay cancellation.

`preset.search` reads the database records, sorts by creation time newest
first, and then applies `limit`. UUID ordering does not affect
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
determines the duration. A new work must last exactly the requested
`composition.duration_in_frames`: the snapshot shows it as `target_duration_frames`
and `sprite.compose` refuses any other length with `INVALID_ARGUMENT` on `/instances`.
Edits start from the accepted version and keep deriving the length from the instances.

For Linux renderer validation, install the locked Bun dependencies in
`server/src/server/remotion`, then run:

```sh
IMV_TEST_RENDERER=1 uv run --locked --project server pytest server/tests/test_remotion_templates.py -q
```

The browser path requires Chromium, Noto CJK fonts, bubblewrap and util-linux.
Linux sandboxes also mount `/var/cache/fontconfig` read-only when it exists, so each
new Chrome can reuse system font metadata instead of rescanning all font files.
Without that directory, fontconfig keeps its normal per-sandbox cache fallback.
Chrome startup has a separate 90-second deadline: the locked Remotion 4.0.523
renderer hardcodes 25 seconds, so `remotion/patches/` carries a Bun dependency patch
for both CommonJS and ESM builds. Run `bun install --frozen-lockfile` in the renderer
directory to apply it after checkout; when upgrading Remotion, reassess the patch.
The outer render deadline (900 seconds for overlays) still covers browser startup
and rendering; this change does not alter per-frame render timeouts.

The isolation is Linux-only: `POST /works`, a parameter patch on a pre-composition
(PR74) version or on a composed version whose player bundle predates injected defaults
(no `/* imv-preview-protocol 1 */` first line) sent to `POST /works/{id}/messages`, the retry of such a parameter job and
`POST /api/sprites/publish` return 503 naming the unsupported platform or the missing
`bwrap`, `prlimit` or `node` before anything is queued, so on macOS the service starts but
cannot generate templates. A parameter patch on a composed (PR76) version whose bundle
reads injected defaults never needs the sandbox (`publication.revisable`; an older bundle
would keep showing its compiled defaults, so that patch is queued, rendered and its bundle
rebuilt, after which later patches are instant): `Runtime` records the job as running outside the FIFO queue and
`PresentationBuilder.revise` checks the patch against the parameter schema, reuses the
base's `Template.tsx`, player bundle and runtime evidence (`tool-validation.json` names the
base in `inherited_from`), regenerates `Export.tsx` with the host-side `export.mjs` (parse
and format only) and seals a `user_parameters` version at once; the version preview page
injects that version's own defaults, and publishing later renders the boundary samples. Questions,
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

A text effect (`kind=text`) must also show its longest allowed text in full. In the same mount `sprite.create`
fills the `text` parameter with `测` up to its declared `maxLength` and, on the same five sampled frames, measures
the text boxes of the layer: text cut off by an ancestor whose overflow is not visible (`hidden`, `clip`, `auto` or `scroll`: the
video never scrolls) or with `contain: paint`, text outside the 1080x1920 canvas, text lines shorter than 20px (the smallest `fontSize` is 24) and text of which only part is rendered (a
slice of it, or some of it hidden) are problems; text in a node without a box (`display: none`), with
`visibility: hidden` or faded out to opacity 0 is not shown. Each line is measured as its line box (at least as tall
as its font size), not as the font's whole ascent and descent, so a line in a mask exactly one line tall is not
clipped (with Noto Sans CJK the ascent and descent span 1.448em and stick out of a `lineHeight` 1 or 1.2 line).
The text is judged on the frames that show it at its full size (by the area of its lines, within 2% of the
largest): entrance and exit animations show it smaller, scaled down or typed in, and must not hide a layout that
clips it while it holds. The `text_fit` check fails when every one of those frames has a problem, so a reveal mask
or slide-in that moves the full-size text out of view for a few frames is fine, while a fixed-size box that clips
long text whenever it is shown at full size is not; it also fails when the filler text is never visibly rendered. A failure is refused with `CODE_VALIDATION_FAILED` (`text_fit: ...` with the clipped box or
size and the fix: wrap the text inside a bounded width, no fixed-height `overflow: hidden` box, shrink the font
for long text, never below 24px; text that cannot fit even then means the declared `maxLength` is too large and must be lowered, and input beyond it is refused by the parameter Schema). It is a geometric measurement, not a visual review; like the transparency
measurement it only applies to generation, and already published versions are not re-checked.

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
instances use them. Bundling erases the Presets' TypeScript types and type arguments, so the composed
adapter types every variable, class field and base class `any` and gives each class an `any` index signature; each Preset is still typechecked on its own before it is composed. For an edit, the accepted base's instances are registered as `base_<instance_id>`
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
to call next. A pre-Sprite base registers no base drafts: its edit plan says to write the Preset from
`base.legacy_code` instead of reusing drafts. Plans reference `steps.<id>.outputs.presets` or `steps.<id>.outputs.sprite`, and a reference to
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
`imv.sprite.v1.PublishedSprite` (a row of the shared MySQL table `remotion_sprites`, `sprite_store.py`, plus the
object `imv/remotion/sprites/<id>/interactive.js`), so deleting or editing the source work never changes a
published Sprite. Only the device that owns the source work may publish it (another device's version is a 404). The table is created on first use like the template table and sits in the same database as the
templates that reference its IDs, which is what lets a template save check its `remotion` tracks (422 when a
`sprite_id` is not published or the clips' Sprites do not share one width, height and fps, which an overlay render requires). The preview bundle is an object; when it is missing or no longer matches the recorded
hash (for example Sprites published while bundles were local files), the preview route rebuilds it from the
published source in the isolated worker, stores it and records the new hash, and answers 404 only if that fails.
Overlay renders only need the stored TSX.
`DELETE /api/sprites/{id}` removes a publication (anyone may delete from the shared library): its row, bundle object
and cache. While a cloud template's `remotion` track still references it the answer is 409 naming those templates
(raw configurations are scanned, so invalid templates count too); an unknown ID is a 404. The source work and its
versions are untouched, so the version can be saved to the library again.
Publishing re-verifies the sealed version files (409 when changed) and only accepts versions composed
with a kind and top-level parameter bindings (`spec.sprite.kind`/`bindings`); older versions are refused with a
422 that asks for an edit in chat. The requested kind defaults to the agent's choice on the client but may be
changed: the version must expose that kind's required parameters (`categories.REQUIRED`), otherwise the 422 lists
them in Chinese. The generation prompt and required-parameter examples use `text` with `minLength: 1` and
`maxLength: 100`; already published assets retain their own sealed schema and need a new version to change it.
A text Sprite's required parameters include the keyword options the bus forwards from the IMS
title/subtitle keyword fields: `keywords`, `keywordBold`/`Italic`/`Underline`/`Strikeout`, `keywordColor` (`""`
keeps the text colour) and `keywordSize` (0 keeps the font size, otherwise 12–300; placements refuse 1–11), with `keywords` items holding at least 12
characters (the segmentation keywords the bus forwards). Their
defaults must be off (`[]`, `false`, `""`, `0`), checked by `sprite.create` and again at publish (a keyword option turned on in the workspace parameter panel must be
turned off again before saving to the asset library), so an unused keyword option keeps the plain text style. A text Sprite fixes `text_prop` to `text` and `keywords_prop` to `keywords` (text versions now always bind it;
`""` remains only for versions without a keywords binding, which only re-saves of older publications reach); other
kinds leave both empty. Every bound top-level parameter, text and
keywords included, becomes a `VISIBLE_EDITABLE` control with its bounds, enum, colour or integer format (an
`integer` schema is marked `SPRITE_PARAMETER_FORMAT_INTEGER`, since `number_value` cannot tell them apart, and
overrides with a fraction are refused), string lengths and list size; the instances' remaining nested props stay internal. `content_box` records the union of the instances'
layout boxes clipped to the canvas, the pivot of the host transform; summaries of older publications report the
whole canvas. Re-saving looks up the existing publication before any of these rules, so a publication made before
effect categories can still be re-saved to refresh its player bundle; for a version without a kind any earlier
publication of the same kind counts, because its text field was a free dot path the client no longer sends.
A new publication first renders boundary samples (`boundary.py`, `ToolValidator.validate_samples`,
the worker's `samples` mode): the defaults, every top-level parameter at its low and at its high end (numbers at
their bounds, first and last enum values, colours black and white, text filled to `maxLength` with a CJK glyph or
cut to `minLength`, keywords full or empty, the keyword colour `""` or white) combined with the host transform at its
extremes, and the middle enum values. A text Sprite also renders `keywords:styled`: the low end (longest text, every
keyword slot filled with a hit) with every keyword style, colour and size at its high end, so a styled hit is rendered. When the combined extreme breaks the schema (parameters that constrain each other), it is replaced by the
greedy combination of the extremes that still fit and by one sample per parameter extreme, each with the same end
of the host transform. Each sample must render its first, middle and last frame without an error inside the shared
host layer; a failure is a 422 naming the sample and frame, and a validator that cannot start is a 503 (the route
checks the sandbox first). Bound parameters may only use constraints these extremes respect
(`categories.parameter_problems`), and a version whose root-level constraints still keep some parameter's end or
enum value out of every sample is refused (`boundary.unsampled`). Template overrides are then checked against the
schema only, without the sandbox. The
samples cover the extremes, not every combination: a placement that still fails to render fails its overlay task. The same source and field choice
returns the original Sprite without sampling again. `animation_frames` and `static_frame` stay 0
because composition versions carry no such evidence, and `preview_url` serves the sealed
interactive player page rather than an MP4.

`GET /api/sprites` lists summaries (no TSX). A record that cannot be parsed or fails its hash check is
logged and left out, so one damaged row never hides the library; reading it by ID still reports the
failure. Where a Sprite is placed is not stored here: a placement is a `remotion` track of the cloud template
(`sprite_id`, a seconds start, an optional duration and `editor.remotion` with parameter overrides and a host
transform), saved with the template in one request; the save resolves every placement with `placement.resolve`
(the override must name an editable parameter, keep its type, stay inside its bounds, enum, lengths and list size,
and the merged configuration must still satisfy the published schema; filters and transitions only take opacity;
a publication made before boundary samples, recognisable by its missing `content_box`, lists its parameters as
read-only and only takes the host transform, because its values were never rendered away from the defaults), so deleting a
template removes its placements with it and the composition bus finds them with its single template lookup.
There is no separate route for placements; the template save checks that every `sprite_id` is published. The
SQLite `style_sprite_bindings` table and the `GET/POST /api/sprites/styles/{style_id}` routes of earlier versions
are gone: bindings saved there are neither read nor migrated, and an existing table is left in place. The router
lives in `sprite_router.py` on the main app and borrows the Remotion runtime.

`POST /api/sprites/render` (`imv.sprite.v1.SpriteRenderInput`, Protobuf) renders one Sprite for the bus as one complete
video. A Sprite is a fixed style (the sealed code plus `resolved_style` and `transform`, the placement's editor) with
its content: one `text` with `keywords` for the whole effect (a title), or the bus's subtitle `cues` (text, keywords
and a frame range each; ordered, non-overlapping, at least one frame), each shown once by the same Sprite in its own
frames so the Sprite reads the cue's length as `durationInFrames`, transparent in between. A text effect shown for
less than 1.5 s (`STATIC_TEXT_SECONDS`, most subtitle cues) plays no entrance or exit: it is frozen (`Freeze`) on the
middle frame of the published preview length, which the Sprite still reads as its length, and shows only its own
frames, so short subtitles neither flicker nor run animation code in fewer frames than it was accepted at. The time is
`effect_total_frames`, 0.5 to 120 seconds and the output's frame count, whatever length was previewed when the Sprite
was generated. `sprite_render.py` renders the effect in one overlay worker run that writes consecutive chunks of at
most 30 seconds as frame ranges of the same composition (instances keep their absolute timing, so animations continue
across a boundary) and joins them with `ffmpeg -c copy`, then re-probes the frame count and alpha flag; the bus never
splits a request. Text effects need `text` or `cues`, other
kinds take no content, keywords need a Sprite that publishes them, and content cannot be passed as a style override;
every refusal, a canvas other than the publication's or an unknown Sprite is a 422 at acceptance
(`SpriteRenderAccepted {task_id}`), and the overlay render's environment checks, plus `ffmpeg` when an effect needs joining, are 503s. The task
shares the overlay renders' table, render slot, recovery and callback body below, uploads to ZOS
`imv/sprite_render/{taskId}.webm`, and is read with `GET /api/sprites/render/{taskId}` (`SpriteRenderTask`); each
query endpoint only finds its own kind of task. The overlay worker bundles each distinct Sprite module once, however
many instances use it.
`GET /api/sprites/{id}/preview?overlay=true&sync=1` serves the sealed player on a transparent page for the template editor;
a sync-mode bundle has no controls or loop and seeks on `imv-preview-sync {time, duration?}` messages (`duration` is the
placement's full length in seconds, up to 120; the Player takes it as its length, so a composed Sprite lays itself out
like in the bus render), reporting `sync: true` in its
ready message. The page injects the published defaults and content box as a JSON element (`#imv-preview-host`, with
`<`, `>`, `&` and line separators escaped); the player starts from them, merges `imv-preview-update` values over
them (objects key by key, arrays and scalars replace) and applies an optional `host: {transform}` through
`remotion/host-layer.tsx`, the wrapper the overlay render uses too. Such a bundle reports `host: true` when ready.
`presentation.mjs` writes the host protocol it speaks as the bundle's first line (`/* imv-preview-protocol 1 */`,
`publication.PREVIEW_PROTOCOL`); only that line counts, because the generated code and its defaults are bundled too and
can contain any text. Publishing rebuilds the bundle from the sealed source in the isolated worker when the sealed one
lacks that line and refreshes an existing publication on re-save; a failed rebuild still saves the asset, just without overlay preview.
A refresh stages the new bundle and records its hash before replacing the copy, so a failed update keeps the
previous copy working and publishing again retries it. Identical publish requests (same version and field
choice) take turns, so a double click rebuilds once.

## Overlay renders for the bus

`POST /api/v1/overlay-renders` (`overlay_router.py`) renders **the Remotion clips of a request**
into one transparent VP9 alpha WebM, uploads it to ZOS (`imv/overlay_render/{taskId}.webm`, public-read) and
returns/notifies the public URL. Body (camelCase): `placements` (at most 100, bottom layer first, each
`{spriteId, start, duration?, editor?}`: `start` is a finite number of seconds from 0 and `duration` a number above 0, both
strict like the top-level `duration`; `editor` is the track's editor forwarded as is and may only hold `remotion`
overrides and a host transform, resolved by `placement.py` against the published parameter contract (422 at
acceptance when outside it; without it the Sprite renders its published defaults); the bus reads them from the
template's `remotion` tracks, so a template is looked up once and this module never reads the IMS template library), optional `audioUrl`, `duration` (seconds),
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
- **Text fit**: a generated text effect cannot know how long a title the bus will send, so the host fits the text a
  placement really carries (its `text` override, up to the published `maxLength`) before rendering. For every text
  Sprite with `text` and `fontSize` parameters, inside the render slot, the isolated browser mounts the placement's
  own values at the length its Sprite plays in the render (the length it reads as `durationInFrames`; a held text its
  previewed length; a clip cut by the video's end is measured only on its rendered frames) and measures the text
  boxes the same way `sprite.create` does: cut off by an ancestor whose
  overflow is not visible or that contains its paint, outside the canvas once the placement's host transform moves,
  scales and rotates it, lines under 20px tall, or only part of the text shown, judged on the frames that show the
  text at its full size. Placements that differ only in length or transform are measured separately; the
  transform's opacity does not move text and is ignored. Text that already fits keeps its `fontSize`; otherwise the
  host lowers `fontSize` to the largest whole size, down to the published minimum, at which it is shown in full
  (binary search; the size is logged, the template and the sealed source are untouched). Text that does not fit even
  at the minimum fails the task with `text_too_long` and a fixed message (the callback's `errorMessage`) so the bus can
  tell the user to shorten it, instead of publishing a clipped title. Other Sprites skip the pass, and a fit sandbox
  that cannot start fails the task as `render_failed`. `POST /api/sprites/render` runs the same pass on each distinct
  text or cue (a held text is measured on its frozen frame), and a video composition (`video_composition/remotion.py`)
  on its titles and subtitles: it measures the code it renders (the timing wrapper of a fixed-clock asset) on the
  asset's own canvas, and only the part of that canvas the video shows after scaling it to cover counts as on the
  canvas; the failure there is `remotion_text_too_long`. All text Sprites of a render are measured in one browser
  run, each loaded in turn on the same page, under one deadline: the default validation timeout, or 5 seconds per
  distinct text plus 10 per Sprite when that is longer; the sandbox CPU cap grows with that deadline like an overlay
  render's. It is a geometric measurement, not a visual review.
- **Acceptance**: also a 503 when ZOS, ffprobe, bubblewrap, prlimit, Node, the browser or the managed fonts are
  missing (the isolated renderer is Linux-only), instead of accepting tasks that can only fail later. An empty
  `placements` (`no_placements`) or a `spriteId` that is not published (`sprite_missing`) is a 422.
- **Layout**: every asset must share width/height/fps. Clips are stacked in request order on one canvas (the first
  is the bottom layer), each inside the shared host layer (`remotion/host-layer.tsx`) that pivots around the
  Sprite's content box; each plays for its `duration` (or its own `preview_frames / fps`), which its Sprite reads as `durationInFrames`;
  the composed Sprite lays its instances out against that length (`compose.mjs`), so a longer clip keeps them
  running to its end instead of leaving the canvas blank after the preview length. Each keeps that full length when the total cuts it (so its internal animation ranges are
  unchanged), and is dropped when it starts later. Seconds become frames with half a frame rounding up, like the
  template timing, and a clip is at most `IMV_OVERLAY_MAX_SECONDS` long. Remotion clamps a Sequence to its
  composition, so the worker declares the composition up to the latest clip end and renders only the first `total`
  frames of it.
- **Output**: no audio; the bus/IMS keeps its own audio track and overlays the WebM. The host re-probes the file
  (VP9, size, fps, frame count, alpha tag) before uploading.
- **Execution**: `overlay_render.py` plans, `remotion/overlay-worker.mjs` renders in the bwrap sandbox
  (`IMV_OVERLAY_RENDER_TIMEOUT_SECONDS`, default 900 seconds), `overlay_service.py` owns the MySQL `remotion_overlay_renders` task table,
  up to three renders at a time (each slot covers only the render: the remote audio probe before it and the ZOS upload after it
  run outside, so a slow network never delays queued renders), recovery of unfinished tasks and pending notifications
  when the runtime is first built
  (`routes.runtime_for`, oldest first; a damaged row is logged and skipped, and the overlay routes retry if the table
  could not be read), and ZOS upload via `video_composition.zos.upload_public`. The placements are stored with the
  task, so a recovered task renders exactly what was accepted and only re-reads the published Sprites. State writes retry a locked database
  three times; if it stays unwritable the row keeps its previous state (never rewritten as a failure) and the next
  start re-runs it. Sealed sources are not re-policy-checked in this worker.
- **Worker failures**: an exception exits the isolated overlay worker immediately, including a browser connection
  timeout before `openBrowser()` returns a handle. Bubblewrap cleans up the remaining processes, so the host records
  failure and releases the shared render slot instead of waiting for the full rendering deadline. The private worker
  log records browser startup/connection and sparse rendered/encoded frame counts; API `assembling` remains the
  enclosing video-composition stage and includes waiting for this slot.
- **Cost**: a 1080×1920 frame takes about 0.4–1 s on one Chrome tab (software GL), so a 120 s overlay at 30 FPS needs
  roughly 25–60 minutes and may exceed the default 15-minute deadline. Adjust `IMV_OVERLAY_RENDER_TIMEOUT_SECONDS`
  (at most 3600) for the measured render cost, or lower the length. The single VP9 encoder process and Chrome's GL process each need about
  0.9 CPU seconds per frame, so the sandbox's per-process CPU cap is the render timeout times the host's cores
  (never below the 300 s of the short validation workers) instead of a fixed 300 s.

## Code diagnostics

`GET /api/templates/versions/{id}/diagnostics` re-runs the isolated TypeScript
language service over an accepted version's sealed source and returns its
contract and LSP diagnostics. It re-checks the sealed version files
first, so tampered bytes return 404 rather than a stale conclusion, and an
unavailable sandbox returns 503 instead of reporting "no diagnostics" for code
that was never checked. The route is read-only: it never queues work, publishes
a version or adds a diagnostics table.
