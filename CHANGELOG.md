# Changelog

本项目的版本变更由发布工作流根据 Conventional Commits 自动生成。
推送正式版本 tag 后，成功发布的版本记录会自动写入此文件。

## [v0.3.1] - 2026-09-14
### BREAKING CHANGES
- due to [`885f654`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/885f6541fc777a251c90b7e227d409aee864f3f4) - simplify MVP segmentation pipeline *(commit by [@muyuzhong](https://github.com/muyuzhong))*:

  remove segmentation tuning settings and merge/split trace counters; HTTP field validation now returns FastAPI detail errors.


### New Features
- [`b207213`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b2072134adcf6c440a56159093c0e2f9a0fb4b51) - 新增本地环境 *(commit by [@left0ver](https://github.com/left0ver))*
- [`96ead7b`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/96ead7b0007b8200fd81acc092424bc1053540b0) - **server**: regroup segmentation response fields *(commit by [@muyuzhong](https://github.com/muyuzhong))*

### Bug Fixes
- [`dc29816`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/dc29816a75b0bd9f00c75cc69ed232272e622226) - **server**: restrict localhost CORS port range *(commit by [@tingfeng347](https://github.com/tingfeng347))*
- [`8b08a35`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8b08a35c055b2ba9f020b8839e634e2dbc8f1d54) - **server**: enable HTTP model endpoints by default *(commit by [@muyuzhong](https://github.com/muyuzhong))*

### Refactors
- [`3ff6c6d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3ff6c6df180d8c037213b5a87ed806dddf4eb4d0) - **client**: replace global Tauri API with module imports *(commit by [@left0ver](https://github.com/left0ver))*
- [`885f654`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/885f6541fc777a251c90b7e227d409aee864f3f4) - **server**: simplify MVP segmentation pipeline *(commit by [@muyuzhong](https://github.com/muyuzhong))*

### Documentation Changes
- [`80d9ee2`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/80d9ee23716c0a53705d56168651cd75d10ce998) - update CHANGELOG.md for v0.3.0 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`a702159`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a702159ceb7b7ce109cbe9d71a89e71553248dd4) - bump version into v0.3.1 *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

### Other Changes
- [`9dd49ca`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/9dd49ca636c320ac15fb2824aa235ac3a348f151) - Merge pull request [#25](https://github.com/HsiangNianian/IntelligentMixVideo/pull/25) from tingfeng347/fix/cors-port-range

fix(server): restrict localhost CORS port range *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`89716fb`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/89716fbd1a2ab2bdd0b5558f85753d1b82798089) - Merge pull request [#28](https://github.com/HsiangNianian/IntelligentMixVideo/pull/28) from left0ver/feature/add_local_environment

feat(client): 新增本地模板存储与环境切换 *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`5c98ac0`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5c98ac0e43b6e847549904ae92dd2a4a62ec7b12) - Merge pull request [#26](https://github.com/HsiangNianian/IntelligentMixVideo/pull/26) from muyuzhong/feat/segmentation-response-fields

feat(server): regroup segmentation response fields *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`4762609`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4762609b3c17b9a5048de02e22ad4382750bce21) - Merge pull request [#30](https://github.com/HsiangNianian/IntelligentMixVideo/pull/30) from muyuzhong/refactor/segmentation-mvp

refactor(server)!: simplify MVP segmentation pipeline *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`50c6e12`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/50c6e120466f998fd88ee9040e5904e439abf637) - Merge pull request [#29](https://github.com/HsiangNianian/IntelligentMixVideo/pull/29) from HsiangNianian/dev

feat(client): 新增本地模板存储与环境切换 *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`ef92304`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ef923040a41db8a60d0272da0c3d5333e2472a4e) - Merge pull request [#31](https://github.com/HsiangNianian/IntelligentMixVideo/pull/31) from HsiangNianian/dev

chore: bump version into v0.3.1 *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

## [v0.3.0] - 2026-09-12
### Bug Fixes
- [`9cd4c47`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/9cd4c47ecf3b92d00e77522b419c7fb4814ddfcd) - **server**: reconcile async ASR with upstream fixes *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`a55a2b4`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a55a2b4c576ecaba90d87bca70136d3f6a17731c) - **client**: bundle GStreamer plugins in AppImage *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`0dbbe54`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/0dbbe54c281e49b1257924d87db94b811effe9bc) - **client**: restore Windows preview and unblock local editing *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

### Build System
- [`8de7ec6`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8de7ec6730862805d2ae559db454bc85bfd4797d) - disable unused uv cache in release preflight *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

### Documentation Changes
- [`7f6405b`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/7f6405b62ddf1f9606f2d9e131245901c3424730) - update CHANGELOG.md for v0.2.0 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`663231a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/663231a79da46894918c72ec4116ed131bdeb936) - **release**: synchronize client and server versions at 0.3.0 *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`b362e6c`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b362e6cd90e7740ed89a6629e247cb852cdf038a) - **release**: prepare v0.3.0 *(PR [#24](https://github.com/HsiangNianian/IntelligentMixVideo/pull/24) by [@HsiangNianian](https://github.com/HsiangNianian))*

### Other Changes
- [`b134d57`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b134d5787597b7eae53b62fbdbc53645425a3a8c) - fix(server)：将asr改为异步
- [`2d3c8ce`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2d3c8ce1d4b272c93b6a31482e7c9632237af9f3) - Merge branch 'dev' of https://github.com/HsiangNianian/IntelligentMixVideo into dev
- [`ce6cadc`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ce6cadcad740581082fa4ac1a27596068f83bf30) - Merge branch 'dev' of https://github.com/HsiangNianian/IntelligentMixVideo into dev
- [`2e07b61`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2e07b61add3e5bf382f004a4f11b8854e3d1bedd) - Merge pull request [#19](https://github.com/HsiangNianian/IntelligentMixVideo/pull/19) from IT-coder-Yy/dev

fix(server)!: 将 ASR 转写改为异步并支持取消清理 *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`1e5fcdb`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1e5fcdbbaf375476755cd5f3e7307bd51b940aea) - Merge pull request [#21](https://github.com/HsiangNianian/IntelligentMixVideo/pull/21) from muyuzhong/dev

fix(client): bundle GStreamer plugins in AppImage *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

## [v0.2.0] - 2026-09-12
### BREAKING CHANGES
- due to [`a88216b`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a88216bf82399199b7d4669bd5f314c764dac49f) - replace matrix alignment with bounded wavefront search *(commit by [@muyuzhong](https://github.com/muyuzhong))*:

  Replace IMV_SEGMENT_MAX_ALIGNMENT_CELLS with IMV_SEGMENT_MAX_ALIGNMENT_WORK, defaulting to 250000 work units. The old setting is no longer used.

- due to [`0acc0c8`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/0acc0c8dedca35d3335fdb43b2ba920868c1782e) - separate segmentation core from HTTP handling *(commit by [@muyuzhong](https://github.com/muyuzhong))*:

  Segment keywords now contain only text; start and end offsets are no longer returned.

- due to [`db23b8e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/db23b8eedadf1369fbda3ca9d700d250a880945e) - tighten fun-asr input and segmentation prompts *(commit by [@muyuzhong](https://github.com/muyuzhong))*:

  Segmentation no longer accepts top-level sentences or legacy ASR timestamp aliases.


### New Features
- [`6907ccf`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6907ccfdc60dcd7a7612f87f5db32747f9245443) - **server**: start FastAPI with uv run server *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`45ed191`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/45ed191afe6640b23113e3ee641ececfe0e622cd) - **server**: add script and ASR alignment segmentation API
- [`9f4c58a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/9f4c58a7a9e624b03838009d0485c092d3688254) - adopt shadcn UI conventions and pytest coverage *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`046ce1e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/046ce1e6b94bb1a724406374fd1a5e4b310f0ade) - **server**: cap alignment cost before building the matrix
- [`6c2e945`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6c2e9453e0bde303e6d2bfd1a66132aa405cab08) - **server**: 增加asr的实现function
- [`2c4d78b`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2c4d78ba491a76462b3c5dbcefdfc9ad4acbe874) - **server**: 添加模版的接口 *(commit by [@left0ver](https://github.com/left0ver))*
- [`043d22c`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/043d22c9ec6a6abc3426cd33e2338ecfeb6b02af) - **client**: 添加客户端的代码 *(commit by [@left0ver](https://github.com/left0ver))*
- [`44342bb`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/44342bba522f8a243f1a58014bc93f98840748ba) - **client**: 添加时钟 *(commit by [@left0ver](https://github.com/left0ver))*
- [`2218b97`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2218b97714593a339faa20942fe4d83499871955) - add template editing and audio processing *(PR [#15](https://github.com/HsiangNianian/IntelligentMixVideo/pull/15) by [@HsiangNianian](https://github.com/HsiangNianian))*

### Bug Fixes
- [`84280cc`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/84280cc781b8d56d48a0e5956f0b070dcf4ab33b) - **ci**: publish draft releases by release ID *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`1fd5c85`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1fd5c858d29959543bfbb291397a41131a1463e8) - **server**: use PyPI for locked dependency installation *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`cdccadb`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/cdccadb68ca3de59277a2f32c0917c60e0e92d4b) - **server**: 完善文案切片异常处理、测试隔离与开发规范 *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`2558799`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2558799afe52b9c28699f7d28f80f2a16181bd95) - **server**: 完善 ASR 配置加载、请求校验和测试
- [`b44d90e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b44d90e82f5fec161f12b8e96d0bcd850f72406c) - **server**: 精简切片时保留目标分支的索引配置 *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`014d56f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/014d56f9156553a30d0cd959068a5b7c5a70e676) - **server**: 硬编码北京地区的base_url
- [`000fe39`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/000fe39e6820b0e890853d256b8ff4e9bde61eb8) - reconcile template and segmentation integration *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`e5791ff`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e5791ffc3a9048b31ed76792727a6d9c163befed) - preserve ASR alongside template and segmentation features *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`0b57eb7`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/0b57eb7ac398570a45d720aa6eb38d115b0a88fb) - retain validated integration after segmentation sync *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`db23b8e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/db23b8eedadf1369fbda3ca9d700d250a880945e) - **server**: tighten fun-asr input and segmentation prompts *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`a60d2c2`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a60d2c2b1d33d30cc4bb4cb3424368d29b095fa7) - integrate template and segmentation features *(PR [#16](https://github.com/HsiangNianian/IntelligentMixVideo/pull/16) by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`fd64462`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/fd64462fe178dd9ce5fd5b5082045883726b1eae) - **server**: address ASR review edge cases *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

### Performance Improvements
- [`a88216b`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a88216bf82399199b7d4669bd5f314c764dac49f) - **server**: replace matrix alignment with bounded wavefront search *(commit by [@muyuzhong](https://github.com/muyuzhong))*

### Refactors
- [`153fa46`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/153fa463278e3a95f3066dbe0c9e92ff368fde8f) - **server**: consolidate segmentation tests and align documentation *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`57770f6`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/57770f69e3fcdccfffec1bcced0d30cc0c51a1c3) - **server**: 将文案切片收敛为单个函数 *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`0acc0c8`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/0acc0c8dedca35d3335fdb43b2ba920868c1782e) - **server**: separate segmentation core from HTTP handling *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`d86c536`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/d86c53650d9e264c44f2dd6af5f0faacdbfc6fb3) - **server**: load segmentation config with Pydantic Settings *(commit by [@muyuzhong](https://github.com/muyuzhong))*

### Tests
- [`5fc3052`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5fc3052f9c552c4b45f19bcfd963c0e9334ac819) - **server**: 符合agents.md的规范
- [`635b1c7`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/635b1c751046308528ed0f8701b91b94d322ea92) - **server**: 添加服务端的测试代码 *(commit by [@left0ver](https://github.com/left0ver))*
- [`2cbca06`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2cbca06c8463d57bf32dcd4645a99cd7d9ea7d77) - **client**: 添加client的测试 *(commit by [@left0ver](https://github.com/left0ver))*

### Build System
- [`5d1c762`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5d1c762a1d39ce39f0abbde2a4ffb2fee430e531) - deduplicate PR checks and scope client builds *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

### Documentation Changes
- [`10db882`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/10db882c0dcfee3669befbc4bf66ccacc709bf5a) - add contribution guidelines and AGPLv3 license *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`50d707c`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/50d707c5409b12684f63c4b8091832d24efcf348) - link Claude instructions to AGENTS.md *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`5bc77d5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5bc77d5557bc4a3f5845373746c622140ed8dc7c) - align contribution checks with upstream validation *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`43057c8`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/43057c8c892b6b2dce5acfa703c11564c2924b55) - update CHANGELOG.md for v0.1.1 [skip ci] *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`45db1fd`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/45db1fd7f2b3a22a645760000d2e5b3c574969fb) - align segmentation input and prompt guidance *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

### Chores
- [`6720f2e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6720f2ec97966a393be0b1d8c9aa8d7987318f6f) - suggestion from @sourcery-ai[bot] *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`f801404`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f801404e77cf6dacb65a9f9f4eddfbf421887a16) - synchronize main and dev branches *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`864cb20`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/864cb2006bd30e81c4ebd75a95aeffc14c97e648) - merge upstream dev
- [`dc979cc`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/dc979cc1b7aff1702307d974980cdde3ab2e19df) - merge remote dev and reconcile segmentation tests *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`cd787d3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/cd787d3f2a3fecf45d7e33d11b9ce7dbaf1d99af) - 添加模版配置的.proto文件 *(commit by [@left0ver](https://github.com/left0ver))*
- [`46e25d7`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/46e25d7cba0179138bc60b250c14f02c2104395d) - prepare shared files for feature integration *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`c31a178`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c31a1785b6d6a5be9ab42e345459d723264f0ad9) - integrate segmentation and template histories *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`55d95ca`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/55d95ca60133b9d37b7003bd05a6ff203c87d637) - prepare shared files for latest dev integration *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`03bf6c4`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/03bf6c46f1aeb19982a27744845f0b97a7754a4c) - merge latest dev with ASR integration *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`927bf97`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/927bf97d6e38d4fd3112f6952a4b190bd73e0f1c) - prepare latest segmentation history integration *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`81e8c44`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/81e8c444bcf84a6c130fcc05f27de36b60c6c215) - preserve latest segmentation branch history *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`c710441`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c710441bbfd37f06331531c9eb01ec643fe56f74) - integrate latest fun-asr segmentation contract *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

### Other Changes
- [`f80d28b`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f80d28b405bbb23ebd0dd0fd53c3d839ce6e19e2) - Merge pull request [#2](https://github.com/HsiangNianian/IntelligentMixVideo/pull/2) from jyh20030112/dev

docs: add contribution guidelines and AGPLv3 license *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`66d9f73`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/66d9f73a22360a55dfb936fb4d07866e736e772f) - Merge pull request [#4](https://github.com/HsiangNianian/IntelligentMixVideo/pull/4) from HsiangNianian/dev

feat(server): start FastAPI with uv run server *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`0104911`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/0104911626d68b186d5f7ceaea98ad92582b742c) - Merge pull request [#5](https://github.com/HsiangNianian/IntelligentMixVideo/pull/5) from jyh20030112/fix/server-pypi-index

fix(server): use PyPI for locked dependency installation *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`3f55d8d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3f55d8d41df4914d2507cf4ed3d744105de6cae5) - Merge pull request [#6](https://github.com/HsiangNianian/IntelligentMixVideo/pull/6) from IT-coder-Yy/dev

feat(server): 新增 DashScope 音频转写功能及 pytest 测试 *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`6e3946a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6e3946ae290323cea65a946849c7cb190febfac2) - Merge pull request [#17](https://github.com/HsiangNianian/IntelligentMixVideo/pull/17) from left0ver/feature/clock

feat(client): 在模板工作区页头添加时钟 *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

## [v0.1.1] - 2026-09-11
### Bug Fixes
- [`5204ffd`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5204ffdd224fdda1569f50566e2ca68e706f060e) - **ci**: resolve Cargo manifest during release validation *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

[v0.1.1]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.1.0...v0.1.1

[v0.2.0]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.1.1...v0.2.0

[v0.3.0]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.2.0...v0.3.0

[v0.3.1]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.3.0...v0.3.1
