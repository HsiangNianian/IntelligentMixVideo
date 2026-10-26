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
| `preset.modify` | implemented: copies a stored Preset or a task draft (`base_*`, earlier `draft_id`), keeps the copy host-side and returns its `draft_id`; compose it with source `{kind: "draft", draft_id}`. Code changes go in as exact `edits` `[{old_str, new_str}]` (each `old_str` occurs once) or as a whole `code`; the receipt counts the changed code lines and notes an edit that copied a stored Preset instead of its `base_*` draft (a stored Preset with the same code, schema and defaults as a `base_*` draft is the one the base draws with and is not noted) |
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
startup interrupts jobs a previous process left running and finishes interrupted deletions. A lease row
(`remotion_instance`) enforces it: startup acquires the lease or the first Remotion request answers 503 ("another
backend instance is using this database's queue"), the instance renews it every 10 seconds and releases it on a clean
shutdown. A crashed instance's lease expires after 30 seconds, so a restart after `kill -9` may have to wait that long.
An instance that finds its lease taken (it stopped renewing, for example during a long database outage) stops
claiming jobs and sweeping. A second backend on the same database (for example a Debug desktop backend reading the
same `server/.env`) is therefore refused instead of interrupting the first one's running jobs. MySQL 8.0 or later is required: acknowledged events are deleted and clients resume after the last event
ID, which relies on InnoDB keeping its AUTO_INCREMENT counter across restarts (5.7 resets it to the highest row).

Every work belongs to the client device that created it. JSON routes of a work require the `X-IMV-Device`
header (a UUID the client keeps in its local session database) and treat another device's work as missing. A device
is recorded when it creates its first work and its activity time is refreshed at most hourly; other header values add
no rows;
browser-embedded reads (preview page, fonts, reference image, artifact download) are addressed by unguessable IDs.
Chat history lives on the client: the server buffers public messages and events only until the client stores them
locally and acknowledges the last event ID with `POST /works/{id}/ack`. The open work streams its events over SSE
(`GET /works/{id}/stream`; an idle stream sleeps until a commit appends an event of its work, with a 5 second read as a safety net, so it costs the database almost nothing); works the client is not showing catch up through `GET /works/{id}/events?after=<id>`,
one page per call in the same shape, until an empty page. There is no server-side work list: the sidebar reads the
client's local store. Acknowledged events are deleted; once a work
has no undelivered events, the progress, rounds and messages of its finished jobs go too, and only the latest job row
stays because retrying it or answering its questions needs its input.

Sealed version files, reference images and Sprite preview bundles are objects under `imv/remotion/`
(`object_store.py`): public-read in ZOS once `ZOS_BUCKET` is set, otherwise files under `IMV_DATA_DIR/objects`.
Every sealed file of a version is public-read under `imv/remotion/versions/<version_id>/`, including the internal
`candidate.json`, `spec.json` and `tool-validation.json` that the artifact route never serves; anyone who knows a
version ID can read them, and `GET /api/sprites` lists each published Sprite's source version ID. Chat reference
images are public-read under unguessable IDs as well.
The backend reads them through a verified cache in `IMV_DATA_DIR/cache` (SHA-256 against the MySQL record), one file
at a time (deciding whether a parameter patch is instant reads only `interactive.js`). Object uploads, downloads and
deletes run in worker threads and never under the write lock: a version is uploaded under its new ID before its record
commits and removed again if the commit fails; deletion and the upload sweep remove records and objects in separate
steps. ZOS objects are only removed when `ZOS_DELETE_OBJECTS` is true; by default a ZOS prefix delete sends nothing,
since the store bills deletions (Aliyun OSS), so records and local caches go and the objects are left unreferenced. A ZOS failure other than a missing object is an `ObjectStoreError` (an `OSError`): uploads, reference images,
downloads, diagnostics and work deletion answer a retryable 503 instead of a 500 or a misleading 422. Prefix deletes
list the objects and remove each page with one batch request that carries `Content-MD5` (`zos.storage_client` swaps
in the header botocore 1.36+ replaced with a CRC32 checksum; Aliyun OSS and similar S3-compatible stores reject a
batch delete without it). Job
scratch is removed when the job ends and the private `audit/<work_id>/<job_id>.jsonl` log outlives it (deleting the
work removes its whole audit folder, including logs of jobs already pruned). An hourly sweep (started with the runtime on the first Remotion request, which runs it immediately) applies
three retention periods: undelivered events (`IMV_UNDELIVERED_EVENT_DAYS`, 7), audit logs
(`IMV_AUDIT_RETENTION_DAYS`, 14) and the works of devices inactive for `IMV_DEVICE_RETENTION_DAYS` (90). The same
sweep retries every deletion whose cleanup failed (an expired device's work, or a user DELETE answered 503 and never
repeated), drops finished overlay render tasks with nothing left to deliver after 30 days
(`overlay_service.FINISHED_RETENTION`) and removes read-cache files unused for 30 days (`store.CACHE_IDLE`; every hit
refreshes a file's time). Each step and each work is isolated, so one storage failure only postpones itself to the
next hour.
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

Version previews accept `?overlay=true` to remove the page's checkerboard while reusing the sealed player bundle.
The client plays URL or local-file backgrounds in a separate bottom layer; these videos are not sent to the player or Agent.

The preview is built from the saved Sprite's exact code, schema and defaults.
The main canvas is 1080×1920 at 30 FPS and the last instance's exclusive end
determines the duration. A new work must last exactly the requested
`composition.duration_in_frames`: the snapshot shows it as `target_duration_frames`
and `sprite.compose` refuses any other length with `INVALID_ARGUMENT` on `/instances` (`/text/duration_frames` for a text block).
Edits start from the accepted version and keep deriving the length from the instances.

### Text effects are composed from role Presets

A new text effect (kind `text`) is composed from a text block (`sprite.compose` contract v3, `text`), not from boxes;
boxes stay for edits of older text effects. A Preset may carry a role. A `text` Preset draws one tier: the host passes
it `text`, the `segments` of that text cut at the keyword hits, a `Keyword` component for the hit segments and
`fontFamily`, `fontWeight` and `fontSize` (`tools/contracts.ROLE_PROPS`), and the Executor rules have it draw the
tier's look (colour, stroke, shadow, gradient, underline) on its own block so the `Keyword` segments inherit it; its `parameter_schema` declares only its
own style props, declaring a host prop is refused, and the code check type-checks it with sample host props (`sprite.create`
re-checks every source Preset with its role). The check's call site passes each default and host prop as its own JSX
attribute, so a literal default such as `"center"` satisfies a prop typed `"left" | "center" | "right"` (an object
spread would widen it to `string`). A
`keyword` Preset draws one hit inline from `text`. The block gives the area, the default text (one line, or
`upper\nlower` for 双栏), the base font size, each tier's Preset, style parameters, font, weight and (two tiers) size
ratio, the tier gap and line starts of two tiers, an optional keyword Preset and the duration. `compose.mjs`
(`textEntry`) writes the host: the tiers are centred in the area, which is also their wrapping width; a tier waits at
its first frame, invisible but keeping its space, until its start and then plays from there; the first literal hit
of each keyword in the whole text (earliest first, never overlapping) is wrapped in the six IMS keyword options and,
while `keywordEffect` is on, drawn by the keyword Preset. The host generates the configuration parameters
(`compose.text_configuration`: fixed names, schemas and `categories.FIXED_TITLES` titles): `text`, `fontSize`, one
tier's `fontFamily`/`fontWeight` or each tier's `line{i}FontFamily`/`line{i}FontWeight`/`line{i}SizeRatio` with
`lineGap` and `lineStartFrames`, the seven keyword parameters (off) and, with a keyword Preset, `keywordEffect` (on).
They are bindings targeting the pseudo instance `text`, so publishing and the category check read them like any
other top-level parameter; the edit snapshot leaves them out and hands back `base.text`, the block over `base_*`
drafts with the current configuration values. The Agent binds style parameters only (`color`, or `line1Color` and
`line2Color` for two tiers, keyword looks as `keywordEffect…`), never a configuration name.

Motion is split out of the tiers (contract v4): `text.effects` lists `effect` Presets, each with a Chinese title, a
slot (`enter`, `exit` or `loop`), a default position and, entering or exiting, a length in seconds (0.1–3, default
0.5). An effect Preset receives `children`, the host's `Box` and `progress` (0 not shown, 1 fully shown) and only
restyles what it wraps. The host leaves a wrap point at every position — `block` (the whole column), each tier
(`tiers` for both, `line1`/`line2` for one; two tiers only) and each keyword hit (`keyword`, an inline box) — so
effect k's configuration parameters `effect{k}` (switch), `effect{k}Target` (position) and `effect{k}Seconds` move it
or retime it without regenerating (`categories.EFFECT_PARAMETER`; titles from the effect's title). An entrance rises
from the position's first frame (a tier and its keyword hits count from the tier's start), an exit falls to 0 on the
last frame, a loop stays at 1, and every length is at most half of the frames its position plays less one, so a text
frozen on its middle frame still shows in full. The text block of a new work records `still`: the host then holds
each tier Preset on the middle frame of the frames the tier plays (`compose.mjs` `__Hold`), so motion of its own
never plays and a stored animated text Preset is reused as it is, look kept; compose requires at least one enter and
one exit effect (kept with `enabled: false` for still text), so a block never ends up without motion. The keyword
hits inside a held tier get its live frame back (`__Live`), so a keyword Preset, the switchable keyword effect, and
the effects at `keyword` keep playing. Edits of a text effect made before effects (no `still`, or boxes) keep playing
their tier Presets' own motion and need no effects. The workspace parameter panel shows such a block as two sections,
「文字」 (the text itself) and 「特效」 (one card per effect and the keyword effect, each with its switch, position, length
and own look).

The Agent decides from the request and the reference images whether the block has two tiers and a keyword Preset. The
new-chat switches are a temporary explicit choice: `POST /works` takes `keyword_effect` and `two_tier` (strict booleans,
default false) that reach every job as `user_intent.original_request`; for a new work (`ToolSession.requested`)
compose then requires two tiers or a keyword Preset, and the category check refuses a text without them. An older
Sprite whose own component draws a keyword effect must expose the `keywordEffect` switch defaulting to true. `text_fit`
mounts the default text once more with up to two characters opening its first non-empty line as `keywords`, so the
hit must stay inline and the whole text must still show. In a text block that hit, mounted again on the middle frame
with the six keyword options and the `keywordEffect` switch off, must also look like the rest of its line: the same
inherited text paint (colour, fill, stroke, paint order, shadow, font, letter spacing) and no filter, decoration or
text-clipped background that the line's other text gets from its own elements. A text Preset that strokes only the
spans of the other segments is refused with the differences (`text_fit: … is drawn without its tier's look …`), so
the template preview's sample keyword and the composed title keyword keep the tier's look. Templates only switch the seven keyword parameters (any
other `keyword…` parameter is internal there), so the keyword effect is changed in the workspace and saved to the
library again. `preset.search` matches every whitespace-separated word of the query and filters by role.

For Linux renderer validation, install the locked Bun dependencies in
`server/src/server/remotion`, then run:

```sh
IMV_TEST_RENDERER=1 uv run --locked --project server pytest server/tests/test_remotion_templates.py -q
```

The browser path requires Chromium, Noto CJK fonts, bubblewrap and util-linux.
Linux sandboxes also mount `/var/cache/fontconfig` read-only when it exists, so each
new Chrome can reuse system font metadata instead of rescanning all font files.
Without that directory, fontconfig keeps its normal per-sandbox cache fallback.

### Font library

Effects can use any font of the shared font library, a MySQL registry (`remotion_fonts`, `font_store.py`) whose files
are objects in ZOS (or the local object folder while `ZOS_BUCKET` is empty). It holds three kinds of font and is never
empty: the system default `Noto Sans CJK SC` (registered when the runtime starts, no file), the preinstalled catalog
and the fonts users upload. A row is one font file; a font card is a family with up to four styles (regular, bold,
italic, bold italic, any of them missing), one file each, all rendered under the card's family, so CSS
`font-weight: 700` and `font-style: italic` use the real bold and italic files. A card's first file is keyed by the
family itself and each later file by `family<TAB>style` (the unique column still reserves the card's name). Rows are
never deleted and their family never changes, so a template that names a family keeps rendering; only how a file is
shown (label, preview strip, style) may be corrected. `GET /api/templates/fonts` lists cards with their `faces`; every
face stored by the library has a `file_url` (`GET /api/templates/fonts/{id}`), and the system default's is the installed
regular file, or null when the host does not have it, so the font library's specimen view can load each available
style's own file.

- **Preinstalled fonts**: the 16 free OFL fonts ship inside the backend package as `bundled_fonts/<file>.xz`
  (xz-compressed, about 60 MB). `font_catalog.json` pins each font's uncompressed size and SHA-256 and records where
  the file came from (`url`, provenance only). After the runtime starts, `FontStore.sync` (`fonts.py`) unpacks the
  ones the registry lacks, checks them against the pin, copies them into the object store and the registry, and
  re-syncs hourly. Nothing is downloaded, so a host that cannot reach GitHub gets the same library. A file that is
  missing or does not match its pin is skipped with a warning (`Font <id> was not installed`) and retried at the next
  sync. `IMV_INSTALL_BUNDLED_FONTS=false` installs none. To add a font, put its `.xz` in `bundled_fonts/` and add a
  catalog entry, and add its license text to `bundled_fonts/LICENSES.txt` (OFL requires it to travel with the font files; `tests/test_remotion_fonts.py` checks that the files, the catalog and the licenses agree); an existing row is never changed.
  IMS font names: `Source Han Sans CN` and `Source Han Serif CN` are the genuine Adobe fonts (OFL), registered under
  the exact `Font` value IMS uses, so a text effect and an IMS title can pick the same family. The other IMS fonts
  (Alibaba PuHuiTi, FZFangSong-Z02S, FZHei-B01S, FZKai-Z03S, FZShuSong-Z01S, SimSun, KaiTi, Microsoft YaHei,
  HappyZcool-2016) are not bundled: they are proprietary or their redistribution terms are not confirmed. Anyone
  holding a licence can upload them (the family name inside the file must match).
- **Uploads**: `POST /api/templates/fonts` (multipart `file`, optional `label` and `preview`, `X-IMV-Device`) takes a
  `.ttf`/`.otf` up to `IMV_FONT_UPLOAD_MAX_BYTES` (a larger file is a 413 that states the limit; an object store or
  database failure is a 503 logged as `Font upload failed` with its cause). The backend only reads the font's names from the `name` table with
  bounds-checked Python and never decodes the font; the sandboxed Chrome and the user's own browser do. The family is
  the font's English (default-language) name; the display name is its own Chinese name when it has one, otherwise the
  same family, and never the file name. Duplicates are found by content and uploading is idempotent: a file whose
  SHA-256 is already registered (an upload or a preinstalled font, whoever added it) is not stored again and the
  answer is 200 with that font's card instead of 201, so a retry after a client timeout or another user's copy ends
  where the first upload did (`FontExists` carries the registered font). A file equal to a preinstalled font that
  sync has not installed yet has no card to return and is refused with 422 naming that font. The file's style (name ID 17, else 2) places it: Italic or Oblique makes it an italic style, and the rest
  names the weight. Regular (or no weight) and Bold files, upright or italic, join their family's uploaded card when
  that style is still empty, in any upload order (`Smiley Sans` Oblique is that card's italic). Any other weight is a
  card of its own named after it (`Montserrat Thin`, whose Thin Italic file is that card's italic). A variable font
  ignores its weight words, which only name its default instance. Another version of a filled style, or a name that a
  preinstalled, installed or reserved font carries, goes to the legacy family (name ID 1), that name plus version
  (`Zed Sans v2.000`), or that name numbered from 2 (`Zed Sans (2)`), whichever is free or names an uploaded card with
  that style empty; preinstalled and system cards are never joined. A preinstalled font whose family an earlier
  upload took is skipped by sync with a warning. Uploads registered before the display name and style came from the
  font (labelled with their file name, all regular) get both at the next `FontStore.sync`; a strip that shows the file
  name is dropped (with its object) and the name is shown as text, and the family never changes.
  The preview strip is drawn by the uploader's browser with that display name (`client/src/features/fonts/names.ts`
  applies the same rule and sends it as `label`); a strip drawn with any other text is dropped, and only its alpha is
  kept. Fonts cannot be deleted. The library is shared, like the Sprite library.
- **Rendering**: the sandbox has no network, so the backend caches each font from the object store after checking its
  registry hash (`<data dir>/fonts/files`) and the sandbox mounts that folder read-only at `/imv-fonts` with a generated
  `fonts.conf` (`FONTCONFIG_FILE`). Chrome in every worker (validation, preview build, overlay render, video
  composition) resolves a family by its CSS name. The config holds one scan rule per font file (preinstalled and
  uploaded) that lists the file under its card's family only, so files that carry the same name inside them stay
  apart, including a preinstalled variable font's legacy name such as `Noto Sans SC Thin`, and fontconfig picks among
  a card's files by their own weight and slant (each sandbox scans the folder afresh, its fontconfig cache being in its
  own `/tmp`). The browser preview registers each file as a `FontFace` with its weight and style; player bundles built
  before cards had styles skip italic files (their list entries carry no plain `weight`). macOS development without
  the sandbox does not load them.
- **Agent**: the Executor rules end with the library as an enum read from the database, one entry per card with the
  styles it has (bold and italic text use the card's bold and italic files). A text effect must expose
  `fontFamily` (an enum of library families, defaulting to the font chosen from the reference image or description);
  `sprite.create` checks the enum against the library. The stored Sprite keeps a plain string (`maxLength` 100), since
  the library grows, and a choice is checked against the live library: `placement.resolve` when a template saves or an
  overlay is accepted, `patch_parameters` for a manual revision. The Agent only receives the user's reference images,
  never its own renders. Older text effects without `fontFamily` keep working and show no font picker.
- **Editor**: the choice is a normal parameter override, so it is stored in the template track's
  `editor.remotion.parameters`. The client's `FontPicker` (`features/fonts/`) is used for every `fontFamily` parameter.
- **Preview**: the player page lists the fonts that have a file (`#imv-preview-fonts`) and registers each as a lazy
  `FontFace` served by `GET /api/templates/fonts/{id}`, so only fonts an effect really uses are fetched.
- **Safety**: an overlay render or text preflight whose code or parameters name a library font this host has not
  cached fetches it first and otherwise fails with `font_unavailable` instead of silently drawing the default font.

Chrome startup has a separate 90-second deadline: the locked Remotion 4.0.534
renderer hardcodes 25 seconds, so `remotion/patches/` carries a Bun dependency patch
for both CommonJS and ESM builds. Run `bun install --frozen-lockfile` in the renderer
directory to apply it after checkout; when upgrading Remotion, reassess the patch.
The outer render deadline (1800 seconds for overlays by default) still covers browser startup
and rendering; this change does not alter per-frame render timeouts.

Remotion 4.0.534 supports VP9 parallel encoding and shared-memory capture without GPU access.
Install its matching Headless Shell outside the sandbox with `ensureBrowser()` and point
`IMV_BROWSER_EXECUTABLE` at the returned absolute binary path; an existing system Chrome path is not
automatically replaced. The actual render records `parallel_encoding` and `shared_memory_capture` per
chunk. Rendering defaults live in `settings.py`; `.env.example` lists the deployment resource reserves.

The isolation is Linux-only: `POST /works`, a parameter patch on a pre-composition
(PR74) version or on a composed version whose player bundle predates injected defaults
(no `/* imv-preview-protocol N */` first line) sent to `POST /works/{id}/messages`, the retry of such a parameter job and
`POST /api/sprites/publish` return 503 naming the unsupported platform or the missing
`bwrap`, `prlimit`, `taskset` or `node` before anything is queued, so on macOS the service starts but
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
size and the fix: wrap the text inside a bounded width of about 90 % of the canvas, no fixed-height `overflow: hidden` box, and use `fontSize` as given instead of deriving a size from the character count; `text.maxLength` must remain exactly 100, so text that still cannot fit requires layout and wrapping fixes, never a lower limit). The check fills the text to `maxLength` at the smallest size the host shrinks to (the declared `fontSize` minimum, at least 24), and then measures the default text at the default `fontSize`, which a user sees first and which must fit without the host's help. It is a geometric measurement, not a visual review; like the transparency
measurement it only applies to generation, and already published versions are not re-checked.

A text effect can show a fixed number of lines (2 to 4, `categories.MAX_LINES`), for example a small lead line above
a large headline. Generation only makes two-tier texts (双栏, `categories.TIERS`, a two-tier text block): the
Executor rules define 双栏 as two rows stacked in one column (never side-by-side columns, bars or a frame). Each
tier's font, weight and size ratio (exactly 0.4 to 2; the tier renders at `fontSize` times it, so the host's shrink
scales both) and the tier gap are host configuration, each tier's colour `line1Color`/`line2Color` an Agent style
parameter; a two-tier text exposes no top-level `color`, `fontWeight` or `fontFamily`. On the generation path
(`category_problems` with the font library) the line starts must have exactly two items, every tier parameter must be
present with its schema and the three shared styles must not be bound; publishing requires neither the shared colour
and weight of a multi-line version nor its tier parameters, so earlier multi-line versions still publish. Tier fonts
are opened like `fontFamily` on save and checked against the live library on overrides and parameter revisions.
`text_fit` and the host's fit never shrink `fontSize` below 24 divided by the smallest tier ratio below 1 (the
worker's `readableFontSize`), so a small
lead tier stays readable. Assets published with 3 or 4 lines keep their line count downstream. Such a multi-line text keeps the single `text` parameter, with its lines separated by `\n`, and also
binds `lineStartFrames`, an integer array of exactly N items (`minItems` = `maxItems` = N, each 0 to 3600), so that
line i appears from frame `lineStartFrames[i]` of the instance. A line with no text or no start is not rendered.
All lines are stacked in one column (the text block's host, or one Preset instance of an older text) spaced by `lineGap` (pixels, minimum 0, maximum 200 to
600), which templates may override per placement like `fontSize`, and they share the entrance and exit: line i
enters from its own start and all lines exit together, timed from `durationInFrames`.
`categories.category_problems` accepts it when the default text has exactly N non-empty lines, the default
starts begin at 0 and never decrease and `lineGap` is bound, and it refuses a text whose default has several lines
but no bound `lineStartFrames` (it would be shown as one line); per-line styles are scalar parameters such as
`line2Color`, with per-line sizes as ratios of `fontSize` so the host's shrink scales every line; `categories.line_count` reads N, and is 1 for every
other text effect. With every start at 0, `text_fit` also requires the default lines to be rendered by one composed
instance (`data-imv-instance`) and to be shown and hidden together on every sampled frame, so a lead line drawn by
its own instance, or fading out at a constant frame of its own, is refused with the frame and line numbers. A line
that has not appeared yet keeps its space (opacity 0) from the first frame: with the default lines started evenly
over the preview, an earlier line that moves in the frame a later line appears by more than it moved the frame
before plus 6px (or a tenth of its height), as a centred column does when it only adds a line once it starts, is
refused too.
The line starts are content the host injects, not a style: publishing leaves them out of the editable parameters
(templates cannot override them), boundary samples keep the defaults, and the catalog summary reports
`line_count` so that the template editor and the parameter panel edit the text line by line. `text_fit` fills the
N lines so that together they reach `maxLength`, and shows every line from frame 0 for both measurements.

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
Preset drafts and listed as `base.instances` in the shape `sprite_compose` takes (source
`{kind: "draft", draft_id}`), like `base.text`; only the Executor's snapshot shows their source, each distinct source once. The session
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
code plus a fixed host-authored message. So the chat can show what the model actually
sent, each call also carries the raw `arguments` (4000 characters) and, on failure, the
tool's real error `detail` (2000). This is published on purpose: `arguments` can hold plan
step goals (`tools.plan_execute`) and candidate source (`preset.create`, `preset.modify`,
`sprite.create`), and `detail` is the tool's own error text, which can name server paths or
compiler output. A turn without calls carries the model's reply (`message`) or the reason
its request failed (`error_detail`, the already sanitized `ModelFailure` text); narration
that accompanies calls is not published. Everything is cut with `…`, so a cut `arguments`
is no longer valid JSON. Tool results, host steer text and the full payloads stay in the
private audit. A terminal job accepts no further rounds. Unknown tool
names become `unknown` with the name the model asked for in `requested_tool`; model
protocol failures are explicitly marked. Each `job.round` event carries one
round, `job.updated` omits that history, and the client keeps the rounds it has stored locally.

## Sprite assets

`POST /api/sprites/publish` copies an accepted version into an immutable
`imv.sprite.v1.PublishedSprite` (a row of the shared MySQL table `remotion_sprites`, `sprite_store.py`, plus the
object `imv/remotion/sprites/<id>/interactive.js`), so deleting or editing the source work never changes a
published Sprite. Only the device that owns the source work may publish it (another device's version is a 404). The table is created on first use like the template table and sits in the same database as the
templates that reference its IDs, which is what lets a template save check its `remotion` tracks (422 when a
`sprite_id` is not published or the clips' Sprites do not share one width, height and fps, which an overlay render requires). The preview bundle is an object; when it is missing or no longer matches the recorded
hash (for example Sprites published while bundles were local files), the preview route rebuilds it from the
published source in the isolated worker, stores it and records the new hash, and answers 404 only if that fails.
Previews of the same asset take turns, so a library full of lost bundles rebuilds each once; after a failed rebuild
previews answer 404 for `sprites.REBUILD_COOLDOWN` (5 minutes) instead of starting a worker per request, and an
unreachable object store is a 503 that starts no rebuild. Deletion waits for a rebuild in progress, so the bundle is
never written back after it. Overlay renders only need the stored TSX.
`DELETE /api/sprites/{id}` removes a publication (anyone may delete from the shared library): its row, bundle object
and cache. While a cloud template's `remotion` track still references it the answer is 409 naming those templates
(raw configurations are scanned, so invalid templates count too); an unknown ID is a 404. The bundle objects are
removed before the row (on ZOS only with `ZOS_DELETE_OBJECTS`; by default they stay and only the row and cache go):
when the object store fails the answer is a retryable 503 and the asset stays listed, so a retry finishes it; the store's own error (code, number of failed keys and the store's request ID, Aliyun OSS's
`x-oss-request-id`) is logged as `Sprite files not deleted`. The source work and its versions are untouched, so the version can be saved to the library again.
`POST /api/sprites/{id}/rename` (`imv.sprite.v1.RenameSpriteRequest`, anyone may rename) changes only the catalog name:
trimmed, 1 to 100 characters, not required to be unique (422 otherwise), an unknown ID is a 404. A placed asset can
be renamed since templates store IDs only. The name lives in the stored JSON; `sprite_store.update_data` locks the
row and merges one field, so a rename and a preview-hash refresh never overwrite each other. Republishing the same
version returns the renamed asset.
Publishing re-verifies the sealed version files (409 when changed) and only accepts versions composed
with a kind and top-level parameter bindings (`spec.sprite.kind`/`bindings`); older versions are refused with a
422 that asks for an edit in chat. The requested kind defaults to the agent's choice on the client but may be
changed: the version must expose that kind's required parameters (`categories.REQUIRED`), otherwise the 422 lists
them in Chinese. Generation and new-publication validation require `text.maxLength` to be exactly 100;
already published assets retain their own sealed schema and need a new version to change it.
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
host layer; a failing sample is only logged (sample name, frame) and the publication still succeeds, because
availability comes first, and a validator that cannot start is a 503 (the route checks the sandbox first). Bound
parameters may only use constraints these extremes respect (`categories.parameter_problems`); when root-level
constraints still keep some parameter's end or enum value out of every sample (`boundary.unsampled`) that is
logged too. Template overrides are then checked against the
schema only, without the sandbox. The
samples cover the extremes, not every combination: a placement that still fails to render fails its overlay task. The same source and field choice
returns the original Sprite without sampling again. `animation_frames` and `static_frame` stay 0
because composition versions carry no such evidence, and `preview_url` serves the sealed
interactive player page rather than an MP4.

`GET /api/sprites` lists summaries (no TSX) with `Cache-Control: no-store`, so a CDN or proxy in front of the
backend never serves a catalog from before a deletion. `GET /api/sprites/stream` is an SSE stream that sends
`catalog.changed` on connect and after every committed publish, rename or delete (`sprite_store` wakes the open
streams through `sprite_events`, in-process like the work streams), and a named `ping` every 15 s while idle; the
event carries no catalog, it only tells the template editor to read `GET /api/sprites` again. A record that cannot be parsed or fails its hash check is
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
most 15 seconds as frame ranges of the same composition (instances keep their absolute timing, so animations continue
across a boundary) and joins them with `ffmpeg -c copy`, then re-probes the frame count and alpha flag; the bus never
splits a request. A multi-line text effect groups up to `line_count` consecutive cues instead, starting a new group when
a cue's `sentence` changes (when both cues give one), or when one more line would exceed the text parameter's
`maxLength` or start beyond the line start limit, so cues valid on their own never fail for being grouped. Each group is one instance from its first cue's start to its
last cue's end; its text is the cues' texts one per line, its keywords theirs in order, and `lineStartFrames` holds
each cue's start counted from the group's start, so line i appears when cue i starts and the group clears together.
A held group (shorter than 1.5 s) shows every line from frame 0, and so does a multi-line `text`, which may have at
most `line_count` lines; a cue's text cannot contain a line break. The text fit measures a group only from the frame
its last line appears (`reveal_frame`). Text effects need `text` or `cues`, other
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
`remotion/host-layer.tsx`, the wrapper the overlay render uses too. The wrapper also lifts the clipping of a composed Sprite's instance boxes (the parent of each `data-imv-instance`, which Sprites sealed before this carry as `overflow: hidden`; `player_page` has the same rule for older bundles), so a larger font, keyword size or underline is bounded by the canvas only; a component's own fixed boxes are still measured as before. Such a bundle reports `host: true` when ready.
With `fonts=host` in the page URL the player does not download the managed fonts itself. The page has an opaque origin
(sandbox), so the browser keeps no HTTP cache for it and every overlay and catalog cover would fetch both files again.
Instead it posts `imv-preview-fonts-request`, and the host page answers `imv-preview-fonts` with a Blob per weight
(`400`, `700`) that it read once from `GET /api/sprites/fonts/{weight}` (`Cache-Control: no-cache`: the browser may keep the file but revalidates it by ETag, so a font replaced by a server upgrade is not served stale).
The answer `fonts: null`, or no answer within 30 seconds (the files are large, so only a parent that never answers is given up on),
makes the player fall back to the relative `fonts/{weight}` route.
`presentation.mjs` writes the host protocol it speaks as the bundle's first line (`publication.PREVIEW_PROTOCOL`).
Version 1 (`/* imv-preview-protocol 1 */`) covers frame sync, injected defaults and placement overrides. Version 2
(`/* imv-preview-protocol 2 */`) adds the host fonts. Only that line counts, because the generated code and its
defaults are bundled too and can contain any text (`publication.bundle_protocol`). A version 1 bundle still reads
injected defaults, so parameter revisions stay instant (`revisable`), but it is not current.
Publishing rebuilds the bundle from the sealed source in the isolated worker when the sealed one
is not current and refreshes an existing publication on re-save; a failed rebuild still saves the asset, just without overlay preview.
A refresh stages the new bundle and records its hash before replacing the copy, so a failed update keeps the
previous copy working and publishing again retries it. Identical publish requests (same version and field
choice) take turns, so a double click rebuilds once.
A saved publication whose copy does not declare the current protocol is rebuilt from its published source on its first
preview. Without the protocol line it cannot take placement transforms, so an asset saved before they existed would not
follow position edits in the template editor; a version 1 copy would download its own fonts. A failed rebuild or store
serves the old copy and the next preview retries. Concurrent previews
of one Sprite rebuild once.

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
  Longer than `IMV_OVERLAY_MAX_SECONDS` (default 300 seconds / 5 minutes) is a 422 at acceptance when the length is already known (`duration`
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
  host lowers `fontSize` to the largest whole size, down to the published minimum but never below 24 (smaller lines count as unreadable), at which it is shown in full
  (binary search; the size is logged, the template and the sealed source are untouched). A `keywordSize` above 0 is
  an absolute size, so it shrinks by the same ratio (at least 12, never larger than set), keeping the keyword's
  proportion to the text instead of crowding the smaller text. If at the minimum the text
  still leaves the canvas only because of where it is placed (a long title whose centre sits near the top edge), the
  host moves this render's copy of the placement by the shortest distance that brings it back inside (the shift is
  logged; the saved placement is untouched, and the horizontal position changes only when the text overhangs the
  side). Text that is clipped, too small, or wider or taller than the canvas even then fails the task with
  `text_too_long` and a fixed message (the callback's `errorMessage`) so the bus can
  tell the user to shorten it, instead of publishing a clipped title. Other Sprites skip the pass, and a fit sandbox
  that cannot start fails the task as `render_failed`. `POST /api/sprites/render` runs the same pass on each distinct
  text or cue (a held text is measured on its frozen frame), and a video composition (`video_composition/remotion.py`)
  on its titles and subtitles: it measures the code it renders (the timing wrapper of a fixed-clock asset) on the
  asset's own canvas, and only the part of that canvas the video shows after scaling it to cover counts as on the
  canvas; the failure there is `remotion_text_too_long`. All text Sprites of a render are measured in one browser
  run, each loaded in turn on the same page, under one deadline: the default validation timeout, or 5 seconds per
  distinct text plus 10 per Sprite when that is longer; the sandbox CPU cap grows with that deadline like an overlay
  render's. It is a geometric measurement, not a visual review.
- **Acceptance**: also a 503 when ZOS, ffprobe, bubblewrap, prlimit, taskset, Node, the browser or the managed fonts are
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
  (`IMV_OVERLAY_RENDER_TIMEOUT_SECONDS`, default 1800 seconds / 30 minutes), `overlay_service.py` owns the MySQL `remotion_overlay_renders` task table,
  up to six renders at a time by default (`IMV_OVERLAY_TASK_CONCURRENCY`; each slot covers only the render: the remote audio probe before it and the ZOS upload after it
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
- **Performance**: `IMV_OVERLAY_FRAME_CONCURRENCY` caps pages per chunk (default 4), independently of task slots.
  Admission lowers the actual page count on small hosts or CPU quotas, and passes that same count to the worker.
  `IMV_OVERLAY_CHUNK_CONCURRENCY` caps simultaneous chunks within one video at 6. Single Sprites, full overlays and
  video compositions share fixed 15-second chunks (`IMV_OVERLAY_CHUNKED=true` by default for the latter two).
  Videos up to 15 seconds use one chunk; the final chunk contains the remaining frames.
  Boundaries preserve every frame exactly once. Only multi-chunk outputs require host `ffmpeg` for joining;
  known overlay/Sprite lengths are checked at acceptance. Audio-derived lengths and resumed tasks are checked
  before rendering; a missing tool then produces `render_environment_unavailable`.
  All videos share `IMV_OVERLAY_GLOBAL_CHUNK_CONCURRENCY=36`, allowing six videos with six chunks each when resources permit; admission reduces per-video chunk concurrency to fit
  the CPU/page and memory budgets. `IMV_RENDER_CPU_RESERVE_PERCENT=25` chooses a common CPU affinity subset
  inherited by every sandbox worker and its descendants, respecting parent cgroup v2 quotas. Memory admission leaves
  `IMV_RENDER_MEMORY_RESERVE_PERCENT=25` of effective host/cgroup memory, reserving
  `IMV_OVERLAY_CHUNK_MEMORY_MB=1024` per page plus one extra unit per video.
  Absolute ceilings default to `IMV_RENDER_MAX_CPUS=144` logical CPUs and `IMV_RENDER_MAX_MEMORY_MB=153600`
  (150 GiB): 36 chunks times four pages, plus six extra browser budgets for memory. Admission uses the stricter
  of these ceilings and the remaining host/cgroup budget. Encoder threads also stay within the common CPU
  affinity range; thread counts do not grant additional CPUs. These are conservative admission budgets, not measured usage.
  At 25% CPU reserve, 128 otherwise unrestricted logical CPUs permit at most 24 four-page chunks; 36 is a ceiling.
  The host memory floor remains 25% of total RAM even when a smaller service cgroup has spare capacity.
  Cgroup quota discovery supports v2 only; v1 falls back to host affinity and memory information.
  The memory estimate is not an allocation cap;
  production should also configure systemd `MemoryHigh`/`MemoryMax` limits for the Python service and its descendants.
  Waiting for resources is bounded by `IMV_OVERLAY_RESOURCE_WAIT_SECONDS=300`; cancellation and failure release grants.
  A timeout fails the task with `resource_unavailable` (`remotion_resource_unavailable` for video compositions)
  and a retry-later message. Callers may resubmit after capacity recovers; this does not automatically retry failed tasks.
  `IMV_OVERLAY_VP9_CPU_USED` and `IMV_OVERLAY_VP9_THREADS` default to 4 and 2 in code, respectively, and can be
  overridden through environment variables. They only tune the actual VP9 encoder. All paths share the same
  worker and copy-only join, preserving absolute animation time.
  Private `performance.json` and the final `Remotion performance` service log separate planning, slot wait, text fit,
  resource wait/granted chunks/reserved memory, preparation, worker, probe, join and upload. The worker records bundle/browser/composition setup and per-chunk
  wall time. `slot_wait_ms` excludes `resource_wait_ms`, so these two wait fields do not overlap.
  With parallel encoding, `rendered_ms` includes waiting for pre-encoding, and `encoded_ms` is only the
  final mux; compare wall time rather than interpreting it as the complete encoding cost. Render deadlines and the
  timeout-times-CPU per-process cap remain in force. Use measured timings for capacity planning.

## Code diagnostics

`GET /api/templates/versions/{id}/diagnostics` re-runs the isolated TypeScript
language service over an accepted version's sealed source and returns its
contract and LSP diagnostics. It re-checks the sealed version files
first, so tampered bytes return 404 rather than a stale conclusion, and an
unavailable sandbox returns 503 instead of reporting "no diagnostics" for code
that was never checked. The route is read-only: it never queues work, publishes
a version or adds a diagnostics table.
