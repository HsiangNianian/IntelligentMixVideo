<h1 align="center">IntelligentMixVideo</h1>

<p align="center">
  <em>An intelligent video remixing agent system built on Alibaba Cloud IMS, with custom templates, server-side text segmentation, media retrieval, and agent-generated orchestration and Remotion effects.</em>
</p>
<p align="center">
  <a href="https://github.com/HsiangNianian/IntelligentMixVideo/stargazers"><img src="https://img.shields.io/github/stars/HsiangNianian/IntelligentMixVideo?label=Stars" alt="GitHub Stars"></a>
  <a href="https://github.com/HsiangNianian/IntelligentMixVideo/releases/latest"><img src="https://img.shields.io/github/v/release/HsiangNianian/IntelligentMixVideo?style=flat&amp;logo=github&amp;label=Release&amp;labelColor=161B22&amp;color=2EA043" alt="Latest release"></a>
  <a href="https://github.com/HsiangNianian/IntelligentMixVideo/actions/workflows/validation.yml"><img src="https://img.shields.io/github/actions/workflow/status/HsiangNianian/IntelligentMixVideo/validation.yml?style=flat&amp;logo=githubactions&amp;label=CI&amp;labelColor=161B22" alt="Project validation status"></a>
  <a href="LICENSE.md"><img src="https://img.shields.io/badge/license-AGPL--3.0--only-blue" alt="AGPL-3.0-only"></a>
</p>

<p align="center">
  <strong>English</strong> · <a href="README.zh.md">中文</a>
</p>

---

## What It Does

IntelligentMixVideo connects desktop template editing, server-side media processing, and cloud video rendering. Create reusable visual templates, then use the server APIs to combine scripts, narration, and video assets into finished videos.

- **Visual template editing**: Add decorative text, speech bubbles, filters, visual effects, transitions, and text animations. Adjust text, font size, position, and effect parameters.
- **Preview and timeline**: Preview template effects, set object start times in seconds or as a percentage of the video duration, and choose a fixed duration or let an object run to the end of the video.
- **Local and cloud template libraries**: Desktop local templates stay on your machine; cloud templates are saved to MySQL through the server. The two libraries operate independently.
- **Text and audio processing**: ASR transcription, text segmentation, and keyword processing provide data for subtitles and composition timelines. Titles and subtitles can each enable Chinese–English text with independent styles; composition translates the actual content and synchronizes both languages. Disabled English settings are retained for reuse and omitted from output.
- **Asynchronous video composition**: Depending on the inputs, use the standard digital avatar workflow or compose media assets with or without narration. Retrieve results through task queries and callbacks.
- **Remotion text effects**: Generate effects with an agent, preview successful versions, and save them as assets for cloud templates.

> Video composition is currently accessed mainly through the server API; the client does not yet have a dedicated page for composing complete videos. Isolated generation of new Remotion effects requires a Linux server and the rendering environment. See [Supported Capabilities](#supported-capabilities) for details.

### Workspace

| Page | Purpose |
| --- | --- |
| Home | Browse local and cloud templates, create templates, and open existing work |
| Template editor | Select assets on the left, preview in the center, edit object parameters and timing on the right, and reorder videos on the timeline below |
| Remotion effects | Request new effects, view successful versions and history, adjust parameters, and save assets |
| Settings | Manage the backend URL and local template path; Debug also provides module settings |

Templates store object configurations and timing rules separately from the preview video. Replacing the preview video recalculates object visibility intervals based on the new video duration.

---

## Workflow

Template editing, video composition, and Remotion effects each handle a different stage:

- **Template editing**: Add text and effects in the client, set object intervals, and save to the desktop local library or cloud library. Only the template configuration is saved, not the preview video.
- **Video composition**: Call the server API with a cloud template ID, script, and media URLs. Based on the input mode, the server performs ASR, text segmentation, asset matching or sequential arrangement, submits the timeline to IMS, transfers the rendered video to ZOS, and returns the result through queries or callbacks.
- **Remotion effects**: Generate and validate effects with an agent, save successful versions as fixed assets, and add them to a cloud template timeline. A separate [overlay rendering API](#rendering-transparent-overlays) exports the full set of Remotion clips as a transparent video.

---

## Quick Start

Choose a setup based on what you want to do:

| Goal | Requirements |
| --- | --- |
| Edit and save local templates on the desktop | Desktop client; local template storage does not require the Python API or MySQL |
| Use shared templates or server APIs | A client or API caller, an accessible server, and MySQL |
| Generate new Remotion effects | A Linux server with model configuration and an isolated rendering environment |
| Compose videos in the cloud | IMS, ZOS, and any ASR, segmentation, or asset matching services required by the selected mode |

The SDK, fonts, and sample media used for template previews still require network access. Cloud features also need valid credentials and configuration after installation.

### Using an Installer

Get official versions from [Releases](https://github.com/HsiangNianian/IntelligentMixVideo/releases). Development builds are available under Artifacts for the corresponding [GitHub Actions](https://github.com/HsiangNianian/IntelligentMixVideo/actions) run and are generally retained for 14 days.

| Platform | Architecture | Installer |
| --- | --- | --- |
| Windows | x64 | NSIS `.exe`, `.msi` |
| Linux | x64 | `.deb`, `.AppImage` |
| macOS | Apple Silicon, Intel | `.dmg` for the corresponding architecture |

The two installer variants use different backend setups:

| Package | Backend | Usage |
| --- | --- | --- |
| Standard | Connects to an external API | Supports desktop local templates; cloud templates, effects, and composition require an available backend |
| Debug | Bundles an API, a private database, and runtimes | On first launch, extracts the runtimes, initializes the database, and waits for the API to become ready; connects to an external backend instead if an explicit URL is saved |

Debug installer artifacts are named `intelligent-mix-video-debug-<platform>-<commit SHA>`. They use optimized builds; the main difference is the bundled backend. The Linux bundled backend requires glibc 2.35 or later. First-time initialization may take a while.

After installing and opening the client, follow [Basic Usage](#basic-usage) to create a local template. For cloud features, check the backend URL and related configuration in Settings.

Official clients expose version checks and one-click updates in Settings. Compatible, validated UI updates keep the window open; native updates install and restart automatically (deb uses a download guide). Configure release signing and record platform validation before enabling hot updates; see the [client update guide](docs/client-update-design.md).

Windows code signing and macOS notarization are not currently configured. See the [client documentation](client/README.md) and [Builds and Releases](#builds-and-releases) for platform setup and build details.

---

### Running from Source

Client development requires Bun, Rust stable, and the [Tauri 2 platform prerequisites](https://v2.tauri.app/start/prerequisites/). Use the Bun version specified by `packageManager` in [client/package.json](client/package.json). The server requires Python 3.12 or later, [uv](https://docs.astral.sh/uv/), and MySQL.

- Windows: Install Visual Studio C++ Build Tools, the Windows SDK, and WebView2. ARM64 hosts also need the matching ARM64 C++ tools.
- macOS: Install Xcode Command Line Tools and prepare the environment as described in the Tauri documentation.
- Linux: Install the system dependencies for your distribution as described in the Tauri documentation.

If you have not cloned the repository yet, run:

```bash
git clone https://github.com/HsiangNianian/IntelligentMixVideo.git
cd IntelligentMixVideo
```

The following steps start at the root of the checked-out repository. If you already have a local checkout, use it directly. Available features and configuration depend on the documentation included with the selected branch or version.

---

### Running the Server

Skip this step if you only use desktop local templates. For cloud templates or APIs:

1. Start MySQL.
2. For initial setup, copy `server/.env.example` to `server/.env`. If the configuration file already exists, edit it directly.
3. Set `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME`.
4. Run the following from a terminal at the repository root:

```bash
uv sync --locked --project server
uv run --locked --project server server
```

The server listens on `0.0.0.0:20070` by default. Once it starts, open the [API documentation](http://127.0.0.1:20070/docs) on the same machine. Startup creates the database if it is missing, so the configured database account needs permission to create it.

Remote clients must use the server IP address or domain name, and the server port must be accessible. See the [server documentation](server/README.md) for database, API, and configuration details.

---

### Running the Client

Open another terminal and enter `client/` from the repository root. For initial setup, you can copy `client/.env.example` to `client/.env`. If the API URL is unset or empty, it defaults to `http://localhost:20070`.

```bash
cd client
bun install --frozen-lockfile
bun run tauri dev
```

This opens the desktop development window. Create or select a template from Home. Editing local templates does not require a separately running server.

You can also use browser development mode from `client/`:

```bash
bun run dev
```

Then visit [http://localhost:1420](http://localhost:1420). Browser mode does not support the desktop local template library; cloud templates still require a server connection.

---

## Basic Usage

### Creating and Saving a Template

1. On Home, create a local or cloud template and enter its name and description.
2. A new template includes one title and one subtitle. Select styles and effects from the asset panel on the left; VFX is limited to one object, and multiple filters are allowed.
3. Select an object and adjust its text, font size, position, style, and animation on the right.
4. Set object timing in the inspector on the right and preview it in the center. The subtitle always covers the full video. Drag video clips on the timeline to change their order.
5. Click Save. The first save creates the template record; later saves update the same template.
6. Return to Home and confirm that the template appears in the appropriate local or cloud library.

Local and cloud templates do not sync automatically. Switching templates or creating a new one prompts you to handle unsaved changes. Editing does not provide autosave or crash recovery.

The preview uses the first ten seconds of each of three videos, or the full source when it is shorter; local files picked in the dialog keep their full length. Load all three URLs through the preview dialog, or pick a local file for any of them (kept only on this machine; the desktop client keeps it across restarts). The first transition connects videos 1 and 2; the second connects videos 2 and 3. Edit their effects and durations in the inspector. The timeline lists title, subtitle, bubbles, transitions, VFX, filters, and videos from top to bottom. Preview sources and video order are independent of saved templates.

Default and custom sources both use their actual media durations. Template rules and media information produce a shared timing result: preview duration subtracts transition overlaps, while composition preserves the fixed audio and material timing. Transitions require a boundary and duration; unavailable boundaries produce an explanation. Cloud and local saves enforce the same object counts and timing rules. Invalid stored templates remain visible with their ID, validation reason, and individual delete action; valid templates remain usable. Upgrade the client and server together.

See [template behavior](client/README.md#模板行为) for timing rules, asset operations, and save constraints.

### Using Remotion Effects

Configure Remotion Agent on the server (or in Debug client Settings) and confirm that the connected Linux server has the isolated rendering environment. Then open Remotion effects to request a generation, view successful versions, and adjust parameters.

Save a successful version as an asset, then add it to a cloud template timeline from Remotion assets. Local templates do not currently support these assets. Previewing effects and saving assets do not submit a full cloud video composition task.

See the [Remotion client documentation](client/src/features/remotion_templates/README.md) for usage and version restrictions.

### ASR and Text Segmentation

ASR provides a standalone asynchronous Python function and a command-line entry point. It uses Fun-ASR in the Beijing region to process direct HTTPS audio URLs and returns the original JSON with character and word timestamps. There is currently no standalone HTTP transcription endpoint.

Set `DASHSCOPE_API_KEY` in `server/.env`, then run from the repository root:

```bash
uv run --locked --project server python -m server.asr "https://example.com/audio.wav"
```

Replace the example URL with a direct audio URL accessible to the cloud service. The result is written to `asr_result.json` in the working directory, overwriting any file with the same name.

`POST /segmentations` uses a correct script and an existing ASR result to produce segments with timestamps and keywords; it does not call ASR. Full segmentation requires a valid `script` and the original Fun-ASR result with exactly one `transcripts` entry. Word-level `begin_time/end_time` values are in milliseconds; returned segment times are in seconds.

Segments longer than 8 characters, excluding punctuation and whitespace, are split further in code. Without ASR, a request containing only `title` extracts only a title keyword. See [text segmentation](server/README.md#文案切片) and [ASR transcription](server/README.md#asr-音频转写) for examples and constraints.

### Composing Videos through the API

Create a task with `POST /api/v1/video-compositions`, then query its result with `GET /api/v1/video-compositions/{task_id}`. You can also provide `callbackUrl` to receive a terminal-state notification.

The server selects and saves the workflow from the script field and top-level narration URL; do not supply `compositionMode`:

| Input | Workflow |
| --- | --- |
| Non-blank `text` and a top-level `audioUrl` | Standard mode; requires `videoUrl`. Omitting `materials` uses only the digital avatar, while an explicit array participates in asset matching |
| No non-blank `text`, but a top-level `audioUrl` | Media-only mode with narration; optional `copy` supplies subtitles. Requires a non-empty asset list and composes assets in order, regardless of `videoUrl` |
| Neither a narration URL nor a non-blank script | Media-only mode without narration; requires a non-empty asset list and a valid target duration, and does not generate subtitles |

`text` and `copy` are distinct inputs: both being non-blank, or either being non-blank without `audioUrl`, returns HTTP 422 before task creation. Missing, null, empty, and whitespace-only scripts count as absent. The internal mode is saved in `video_compositions.data.request.compositionMode` before `copy` is normalized to `text`; subsequent processing uses that saved mode. Older tasks without it retain the original URL-based interpretation.

A successful creation response means the request was accepted, not that rendering is complete. Its `data` field contains the task ID. Save that ID and retrieve the result through the query endpoint or terminal callback.

`styleId` must identify a saved cloud template; desktop local templates cannot be used directly for server-side composition. Standard mode still requires a valid script, `videoUrl`, and `audioUrl`. Omitting `materials` skips asset matching; explicitly supplying an empty or non-empty array invokes matching. `materials: null` is not accepted.

For asset matching, `COMPOSITION_PUBLIC_BASE_URL` should be an IMV address accessible to the matching service, which uses it to send results back. If empty, the base URL of the composition request is used. The request field `callbackUrl` is the business-system address that receives the final video result.

Terminal callbacks contain `taskId`, `status`, `videoUrl`, and `errorMessage`. A successful callback uses `succeed`, while a successful query result uses `succeeded`. Notification failures do not revert the composition result. Callers can query through GET as a fallback and handle notifications idempotently by task ID.

Required fields, external services, and subtitle behavior differ by mode. Before submitting a request, read the [video composition API documentation](server/src/server/video_composition/api.md), especially the distinction between omitting `materials`, supplying an empty array, and supplying a non-empty array.

The composition integration also renders dynamic Remotion titles and subtitles into one transparent video and adds it to the IMS Timeline. See the [integration and validation notes](docs/remotion-video-composition.md) for the current template-slot behavior and compatibility limits.

### Rendering Transparent Overlays

`POST /api/v1/overlay-renders` renders the fixed Remotion clips saved under a given `styleId` into one transparent VP9 Alpha WebM, using their timing and stacking order. The output contains no audio, and an accessible URL is returned after upload to ZOS.

The request accepts `styleId` and optional `audioUrl`, `duration`, and `callbackUrl` fields. Total duration comes first from the full audio duration at `audioUrl`, then from `duration`, or otherwise from the latest clip end time. Audio is used only to determine the duration; it is not mixed into the output.

Query the status with `GET /api/v1/overlay-renders/{taskId}`. This API always requires a Linux sandbox and the rendering dependencies. See the [Remotion server documentation](server/src/server/remotion_templates/README.md#overlay-renders-for-the-bus) for configuration and limitations.

---

## Configuration

### Common Configuration Options

| Configuration | Location | Purpose and defaults |
| --- | --- | --- |
| `VITE_API_URL` | `client/.env` | Default API URL for source development and builds; defaults to `http://localhost:20070` when empty |
| `VITE_PREVIEW_VIDEO_1_URL`, `VITE_PREVIEW_VIDEO_2_URL`, `VITE_PREVIEW_VIDEO_3_URL` | `client/.env` | Default URLs for videos 1, 2, and 3; leave empty for the corresponding 10-second sample bundled with the client, or use a resource path under `public/` |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | `server/.env` | MySQL connection settings; configure before using the server |
| `PORT` | `server/.env` or process environment | API listening port; defaults to `20070`, with a valid range of 1–65535 |
| `DASHSCOPE_API_KEY` | `server/.env` | API key for Fun-ASR transcription in the Beijing region |
| `IMV_LLM_BASE_URL`, `IMV_LLM_API_KEY`, `IMV_LLM_MODEL` | `server/.env` | Text segmentation model connection, configured separately from Remotion Agent |
| `IMV_ACTOR_BASE_URL`, `IMV_ACTOR_API_KEY`, `IMV_ACTOR_MODEL`, `IMV_VISION_MODEL` | `server/.env` or Debug client Remotion Agent settings | Effect generation and vision models; the client can supply configuration for the current task |
| `SEGMENT_MATCH_BASE_URL` | `server/.env` | External asset matching URL used when standard mode explicitly supplies `materials` |
| `COMPOSITION_PUBLIC_BASE_URL` | `server/.env` | Externally accessible server callback base URL; uses the request base URL when empty |
| `ALIBABA_CLOUD_ACCESS_KEY_ID`, `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | `server/.env` or Debug client Shanghai IMS settings | IMS rendering credentials; optional `ALIBABA_CLOUD_SECURITY_TOKEN`; region and endpoint default to Shanghai |
| `ZOS_API_ENDPOINT`, `ZOS_BUCKET`, `ZOS_ACCESS_KEY_ID`, `ZOS_SECRET_ACCESS_KEY`, `ZOS_WEB_URL` | `server/.env` | Read only by the server; upload and public access URLs are configured separately |

See [server/.env.example](server/.env.example) and [client/.env.example](client/.env.example) for the full field list. Media URL access requirements and asset field constraints are covered in the [video composition API documentation](server/src/server/video_composition/api.md).

Standard client settings show only General, with the backend URL, local template path, and environment information, without requesting the module catalog. Debug mode retains all module settings, including Remotion Agent and Shanghai IMS; database settings are not part of the settings panel. Dynamic forms depend on the configuration schema returned by the backend; displayed defaults are not the actual values in the server `.env` file.

Save applies only the current page and closes the dialog after success; validation or write failures keep it open with the input preserved. Cancel closes the dialog without saving. Closing discards any other unsaved module drafts without undoing values already saved.

### When Changes Take Effect

- **Server running from source**: Configuration is read from the fixed `server/.env` path. Precedence is constructor arguments, process environment variables, configuration file, then field defaults. Restart the server after changes.
- **Frontend environment files**: Restart the development server after editing `client/.env`. Rebuild an already-built client. Other Vite environment files may override variables with the same names.
- **Client backend URL**: Save it under Settings → General. New requests use the new URL; restart the client to switch existing session connections.
- **Debug client model and IMS settings**: Saved values are used by the corresponding business requests. Previously saved module values are preserved in standard clients; without saved settings, server defaults apply.
- **Advanced settings for the Debug bundled server**: Quit and reopen the client after saving; the settings are loaded at startup. This does not apply to manually started or remote servers, and database configuration is outside this settings scope.

Client settings do not modify the shared server `.env` file. See the [client settings documentation](client/src/features/settings/README.md) for the UI, storage locations, and differences between modes.

---

## Supported Capabilities

| Capability | Current scope |
| --- | --- |
| Desktop client | Windows x64, Linux x64, macOS Apple Silicon / Intel |
| Browser development mode | Cloud features through the API; no desktop local template library |
| Local template editing and storage | No Python API or MySQL required; SDK, font, and sample media previews may still need network access |
| New Remotion effect generation | Isolated generation currently requires a Linux server, unprivileged user namespaces, and rendering dependencies |
| Adding Remotion assets to templates | Cloud templates only; local templates are not supported |
| Standalone ASR | Python function and command-line entry point; no standalone HTTP transcription route |
| Video composition | Asynchronous server API; no dedicated client page for complete video composition yet |
| Composition task scheduling | Currently uses a single instance and process; see the server documentation for recovery limitations |
| Cloud template access | The shared template API currently has no login or user isolation |

Windows and macOS Debug packages include runtimes for the bundled API, template storage, and existing previews. Bundled rendering dependencies do not mean that the machine supports isolated generation of new effects.

### Template Compatibility

Templates require a `tracks` array, with each object's parameters stored in `tracks[].editor`. The old top-level `editor` format is not converted automatically.

When an old local template cannot be read, the client shows the file path and explains the effect of deleting it. Back up the file first: deleting it removes every local template stored in that file. Changing the local template path does not migrate existing data.

---

## FAQ

### Cloud Templates Fail to Load

Confirm that the server and MySQL are running and that the client backend URL points to the correct API. If server startup fails, check the database connection and database creation permissions. For access from another machine, also confirm that the port is reachable.

The desktop client can use local templates in the meantime. Local and cloud libraries are independent; switching between them does not move templates automatically.

### Why Can I Not Create Local Templates in the Browser?

Local template storage uses Tauri desktop capabilities. Use cloud templates in browser mode, or start the desktop client.

### Can Local Mode Work Fully Offline?

Local templates can use effects from the bundled catalog and be saved offline. Video previews still depend on access to the SDK, fonts, and selected media. Cloud templates, model calls, and cloud composition require the corresponding services and network access.

### Effect Generation Says the Service Is Not Ready

Confirm that the backend has a working model configuration and a Linux environment for isolated validation and rendering. After updating Agent settings, reconnect from the effects page. Advanced bundled-backend settings require a client restart. See the [Remotion server documentation](server/src/server/remotion_templates/README.md) for dependencies.

### Settings Shows Only General, or the Module List Fails to Load

Standard clients intentionally show only General. In Debug mode, the module list comes from `/api/settings/plugins`; if loading fails, check the backend URL and service status, then reopen Settings. Browser settings are stored only in page memory and are lost on refresh.

### Dependency Installation Reports an Unavailable Version

The server uses the official PyPI index and locks dependencies in `server/uv.lock`. Check for local uv index overrides and mirror synchronization first. Do not work around the issue by immediately downgrading dependencies or deleting the lockfile.

### Where Are Video Composition Logs Stored?

In a source checkout, new task logs are saved under `server/.log/video_composition/<task_id>/`, covering requests, templates, ASR, segmentation, asset matching, timelines, and ZOS.

Each task has `request.json`, `template.json`, `asr.json`, `segmentation.json`, `matching.json`, `timeline.json`, and `zos.json`, all organized into `input/output/execute_log/error_log`. Repeated content may refer to other records in the same task through `$log_ref`.

Logs are written sequentially in the background. Only when the entire `.log` directory exceeds 50 MB are task directories removed from oldest to newest, and only if both composition and notification have finished. Running tasks and tasks with queued log writes are retained. Legacy database log tables are no longer read, written, or migrated; business task tables remain.

Historical logs may still exist under `server/src/server/.log/`; newer versions do not migrate or delete that directory automatically. Script text and signed media URLs in logs may still contain business data. See [execution logs](server/README.md#执行日志) for the structure, retention policy, and troubleshooting.

<details>
<summary>Windows build fails with link: extra operand</summary>

This usually means Cygwin's `link.exe` was used by mistake. In Visual Studio Installer, enable Desktop development with C++ and install the matching MSVC tools and Windows SDK. Rebuild from the Visual Studio Developer PowerShell for the target architecture.

</details>

<details>
<summary>Linux AppImage playback stutters on Wayland</summary>

Run these commands separately under the same conditions to compare the Wayland and X11 backends:

```bash
IMV_GDK_BACKEND=wayland ./app.AppImage
IMV_GDK_BACKEND=x11 ./app.AppImage
```

Replace `app.AppImage` with the actual filename. This option compares display backends; it does not disable hardware acceleration by default. Passing build or artifact checks does not verify playback frame rates on a real desktop.

</details>

<details>
<summary>Linux AppImage cannot open a Chinese input method</summary>

The AppImage loads only its bundled GTK input method modules, never the ones installed on the system (their GTK/GLib versions differ, and mixing them can crash). AppImages up to 0.9.1 bundle no fcitx or ibus module, so no input field can switch to Chinese; upgrade to a newer version. The `.deb` uses the system GTK and is not affected. Newer AppImages bundle the fcitx5 module (which also answers to `GTK_IM_MODULE=fcitx`) and the ibus module. With another input method framework, start the AppImage with the bundled XIM module:

```bash
GTK_IM_MODULE=xim ./app.AppImage
```

XIM requires the input method framework to set `XMODIFIERS` (for example `@im=fcitx`).

</details>

---

## Data and Credentials

- Desktop local templates are stored by default at `data/template/templates.json` under the application data directory. Settings → General lets you choose another absolute `.json` path. Changing the path does not migrate the old file.
- Desktop settings are stored at `data/settings/settings.json` under the application data directory. API keys are currently saved in plaintext alongside other settings; there is no system credential-store integration yet. Browser settings stay in page memory and are lost on refresh.
- Model and IMS credentials supplied through client requests are used per task and are not written to the shared `.env`, work history, or task logs. Logs still contain the actual script and media URLs; check business data before sharing them.
- New composition tasks upload the finished video and a PNG of its third frame to ZOS and make those objects publicly readable. GET responses and successful callbacks return the video URL. The URL stops working if the object is deleted or removed by a lifecycle policy.

See [client settings](client/src/features/settings/README.md) and [video composition](server/README.md#异步视频合成) for storage details and scope.

---

## Development and Documentation

<a id="structure"></a>

### Project Structure

| Directory or file | Contents |
| --- | --- |
| `client/` | Rust + Tauri 2 + React + TypeScript desktop client, using Tailwind CSS 4 and shadcn/ui |
| `server/` | Python + FastAPI + MySQL server providing template, segmentation, effects, and composition APIs |
| `proto/` | Protobuf message definitions shared by the client and server |
| `.github/` | Validation, cross-platform build and release workflows, and supporting scripts |
| `docs/` | Design documents and API agreements for specific areas |

The client is organized into pages, feature components, base UI components, and shared utilities. See [client/README.md](client/README.md#开发结构) for the structure and [AGENTS.md](AGENTS.md#最小改动与源码说明强制) for minimal-change and source documentation requirements.

### Documentation Index

| Topic | Documentation |
| --- | --- |
| Running the client, template editing, and previews | [client/README.md](client/README.md) |
| Server startup, database, and APIs | [server/README.md](server/README.md) |
| Text segmentation and keywords | [Text segmentation](server/README.md#文案切片) |
| Standalone audio transcription | [ASR transcription](server/README.md#asr-音频转写) |
| Video composition modes, fields, and request examples | [Video composition API](server/src/server/video_composition/api.md) |
| Composition execution logs | [Execution logs](server/README.md#执行日志) |
| Client settings, credentials, and when changes take effect | [Settings module](client/src/features/settings/README.md) |
| Remotion page and asset usage | [Remotion client](client/src/features/remotion_templates/README.md) |
| Remotion server and rendering environment | [Remotion server](server/src/server/remotion_templates/README.md) |
| Transparent Remotion overlay rendering | [Overlay rendering](server/src/server/remotion_templates/README.md#overlay-renders-for-the-bus) |
| Collaboration, structure, and build conventions | [AGENTS.md](AGENTS.md) |
| Contribution and PR requirements | [CONTRIBUTE.md](CONTRIBUTE.md) |

<details>
<summary>Template protocol and compatibility</summary>

See [Template Compatibility](#template-compatibility) for the data format and handling of old files. Template configuration is saved separately from preview media.

The cloud template API transfers Protobuf binary messages under `/template`. The root `buf.yaml` manages message definitions and compatibility checks; `buf.gen.yaml` generates client TypeScript, Rust, and server Python code. After changing the protocol, run from the repository root:

```bash
buf lint
buf generate
```

Commit the generated files and run `buf breaking --against <baseline>` against an existing protocol version, replacing `<baseline>` with the actual comparison baseline. See the [template API](server/README.md#模板接口) for the interface format.

</details>

### Pre-commit Checks

Run checks appropriate to your changes:

| Change | Command | Working directory |
| --- | --- | --- |
| Frontend core logic | `bun run test` | `client/` |
| Frontend types and build | `bun run build` | `client/` |
| Server | `uv run --locked --project server pytest server/tests -v` | Repository root |
| Python package | `uv build --project server` | Repository root |
| Rust formatting | `cargo fmt --manifest-path client/src-tauri/Cargo.toml --check` | Repository root |
| Desktop application and installers | `bun run tauri build` | `client/` |
| CI / release scripts | `bun test ./.github/scripts` | Repository root |
| Release workflow regression checks | `bun .github/scripts/release-smoke.mjs` | Repository root |
| GitHub Actions workflows | `actionlint .github/workflows/*.yml` | Repository root |
| Client/server version consistency | `bun .github/scripts/validate-release.mjs` | Repository root |
| Basic file checks | `uvx pre-commit run --all-files` | Repository root |
| Diff formatting | `git diff --check HEAD` | Repository root |

Optionally install local Git hooks with `uvx pre-commit install`.

For documentation-only changes, check the content, links, Markdown preview, and diff formatting; a full application build is unnecessary. Frontend builds and native compilation checks do not replace installer testing or real desktop playback checks. State which checks you ran and what remains unverified.

---

## Builds and Releases

Standard local installers are written to `client/src-tauri/target/release/bundle/`, or the corresponding target subdirectory when a target is specified. Workflow entry points are available in [GitHub Actions](https://github.com/HsiangNianian/IntelligentMixVideo/actions).

### Cross-platform CI

<details>
<summary>Trigger rules, validation scope, and build entry points</summary>

| Event | Checks and builds |
| --- | --- |
| Development branch push | Checks based on changes; skips duplicate push validation when the same commit already has an open PR |
| PR | Runs frontend, server, and any required native compilation checks across four platform/architecture targets based on changes; does not package installers |
| Default branch push | Validates integration as needed; client or CI changes produce standard installers |
| `main` / `dev` push | Debug client additionally produces builds with a bundled backend, independently of standard validation and builds |
| Official version tag | Full cross-platform packaging, attachment validation, and Release publication |
| Manual run | Validate project runs checks, Build client creates standard packages, and Debug client creates packages with a bundled backend |

The regular entry point is [validation.yml](.github/workflows/validation.yml), and builds reuse [client-build.yml](.github/workflows/client-build.yml). The aggregate PR check is named `CI result`. Branch protection should require it and pre-commit.ci rather than individual platform jobs that may be skipped based on changed paths.

When Validate project runs manually, `integration-tests` is enabled by default and uses temporary MySQL and an isolated rendering environment. Regular push / PR runs do not start these heavier tests. Reports and artifacts are retained for 14 days. Enable `build-installers` to create installers in the same run. Set `api-url` for the standard package API URL; leaving it empty defaults to `http://localhost:20070`.

See the [client build CI conventions](AGENTS.md#客户端构建-ci) for scheduling based on client or CI changes, lockfile validation, native checks across four platform/architecture targets, and the integration environment.

</details>

<details>
<summary>Debug bundled backend, data directories, and platform details</summary>

[Debug client](.github/workflows/client-debug.yml) runs on `main` / `dev` pushes and supports manual runs; it does not run on PRs. Builds set the bundled-backend flag through `debug-backend: true`. Standard packages and official releases do not bundle the backend by default. To use the manual web entry point, the workflow must already exist on the default branch; then select the branch to build.

Debug packages include the complete server, Python, uv, a private MySQL instance, Node, Bun, Remotion, Chrome, FFmpeg/ffprobe, and fonts. Linux also includes isolation tools such as bubblewrap/prlimit. First launch extracts resources, initializes the private database, waits for the API to become ready, and connects to its loopback port. A port is assigned automatically if none is saved. Exiting the client shuts down the API and database.

| Platform | Bundled backend data directory |
| --- | --- |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/com.intelligentmixvideo.client/backend/` |
| Windows | `%APPDATA%/com.intelligentmixvideo.client/backend/` |
| macOS | `~/Library/Application Support/com.intelligentmixvideo.client/backend/` |

These directories contain the private database, configuration, and `server.log`; Remotion data lives in the corresponding `remotion/` directory. Initial configuration is created from an example without credentials. Existing configuration and data are preserved, and installers do not include a developer's `.env` or database. See the [build conventions](AGENTS.md#客户端构建-ci) for runtime cache locations and testing scope.

On Unix, the database uses a private socket. On Windows, it uses a random password and a dynamically assigned loopback port. Neither depends on a host MySQL installation. macOS builds are produced separately for Apple Silicon and Intel; Windows uses an x64 native runtime.

Isolated generation of new Remotion effects currently supports Linux servers only. For local development on macOS, set `IMV_ALLOW_UNSANDBOXED_WORKER=true` on the server to run effect generation without isolation. This switch is off by default and ignored on other systems; when enabled, generated code runs outside a sandbox. Transparent overlay rendering always requires the Linux sandbox and is unaffected by this switch. See the [Remotion server documentation](server/src/server/remotion_templates/README.md) for dependencies and configuration.

Linux AppImages exclude bundled Wayland and PulseAudio libraries, use the host versions, and retain the required GStreamer plugins. They bundle the fcitx5 and ibus GTK3 input method modules (the startup hook pins GTK to the bundled module list), and the modules, their libraries and their registration are checked before upload. Startup defaults to `WEBKIT_GST_DMABUF_SINK_DISABLED=1` and `WEBKIT_GST_USE_PLAYBIN3=1` while preserving explicit user settings. Actual playback behavior still needs verification on the target machine.

</details>

<a id="tag-发版"></a>

### Tag Releases

**The client and server share one version, and source versions must match the official tag.** Before release, use the script to synchronize and commit the manifests and lockfiles. After merging, create the `vX.Y.Z` tag at the corresponding commit. Builds validate versions rather than rewriting source versions during the build.

<details>
<summary>Version synchronization and release commands</summary>

Run from the repository root. The following `v0.6.0` is only an example; replace it with the version you intend to release:

```bash
RELEASE_TAG=v0.6.0 bun .github/scripts/validate-release.mjs --write
bun .github/scripts/validate-release.mjs
```

The script synchronizes the project versions in the client `package.json`, `tauri.conf.json`, `Cargo.toml`, and `Cargo.lock`, and the server `pyproject.toml` and `uv.lock`, while preserving third-party dependency versions. In PowerShell, first run `$env:RELEASE_TAG = "v0.6.0"`, then run the same script.

Review and commit the changes, merge them into `main` through a PR, and confirm that the local checkout is at the intended release commit before creating and pushing the tag:

```bash
git tag -a v0.6.0 -m "Release v0.6.0"
git push origin v0.6.0
```

Here, `origin` must point to the project repository that actually publishes releases. Pushing a tag to a personal fork does not release a version of the upstream project.

[release.yml](.github/workflows/release.yml) runs on official version tags. After successful cross-platform builds, it validates the installers, signed update manifests, UI resources, macOS update archives, and `CHANGELOG.md` before making the Release public. Release builds require the `IMV_UPDATE_PUBLIC_KEY` variable and `TAURI_SIGNING_PRIVATE_KEY` secret (plus the password secret when applicable). Only stable versions are accepted, not `-beta` / `-rc` versions, and versions must satisfy Windows MSI numeric limits.

The workflow generates changelogs from Conventional Commits. Publishing uses the built-in `GITHUB_TOKEN` and requires the appropriate write permissions. Failed runs can be retried and missing draft attachments can be uploaded, but reruns do not overwrite published releases. Do not move published tags.

See the [versioning conventions](AGENTS.md#正式版本与-tag-发版) and [Release and CHANGELOG conventions](AGENTS.md#github-release-与-changelog) for version synchronization, concurrent releases, and recovery.

</details>

---

## Contributing

Before starting, read [CONTRIBUTE.md](CONTRIBUTE.md) and [AGENTS.md](AGENTS.md), check existing issues and PRs, and create a separate working branch from the current contribution target branch.

- Use English for commit messages, PR titles, and descriptions. Follow Conventional Commits, for example `docs: clarify setup instructions`.
- Use the [PR template](.github/pull_request_template.md) to explain the changes, rationale, validation, and compatibility.
- Include appropriate tests for features and bug fixes. Documentation should match the actual behavior of its target branch.
- Preserve the existing license and third-party notices, and review the file scope and configuration content before submitting.

When reporting an issue, include the project version or commit, operating system, reproduction steps, and relevant logs.

---

## License

IntelligentMixVideo is licensed under the GNU Affero General Public License,
version 3 only (`AGPL-3.0-only`). See [LICENSE.md](LICENSE.md) for the full terms.
