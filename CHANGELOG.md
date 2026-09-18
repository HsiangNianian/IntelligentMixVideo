# Changelog

本项目的版本变更由发布工作流根据 Conventional Commits 自动生成。
推送正式版本 tag 后，成功发布的版本记录会自动写入此文件。

## [v0.4.1] - 2026-09-18
### New Features
- [`5cbb550`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5cbb550c054ee167b6628c96c1de261952f89e43) - **server**: preserve explicit user parameter revisions *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`b70ead1`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b70ead1b7b8d0ad220048243a139da893b3cbb3d) - **client**: configure Remotion composition before generation *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`3b76d12`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3b76d1297954d50a81ec9a5d14a01835371fe5e7) - **client**: preview parameter drafts and save changes explicitly *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`4f7d158`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4f7d1580ea16a59f7611855572ccf2c91c003e20) - **server**: manage the bundled desktop backend lifecycle *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`8bcc473`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8bcc4731f36b00e63a73993596e8a8922a4fdcb1) - **server**: support bundled libraries in the render sandbox *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`205fd34`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/205fd345bb95e9e981d93c7e8cb4d42d396f28f5) - **client**: start the bundled backend before loading the workspace *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`e4c1929`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e4c19294377d3d9d63cf13d0ec46c54a4c56f77d) - **linux**: allow explicit display backend selection *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`4c05c57`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4c05c5789a0cde962d21863a8d555d53a0fc517c) - **client**: redesign Remotion workspace with version history *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`8ca8b6d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8ca8b6d562e191e38f2c81d32cbb7178d496bb5b) - **client**: add workspace sidebar and settings navigation *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`1fd23ae`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1fd23ae0d84c985fb571a8ae4d9d0abfe01fe4a4) - **settings**: add client-local configuration plugins *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`1ec3802`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1ec38021a6e6b0c770e096591ef36d6ff2d7f4b3) - **settings**: discover module settings and add ASR preview *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`4c6589d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4c6589db371187c867b7ede53f25056a5a3a8bfe) - **settings**: use client credentials for Remotion and IMS *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`a759293`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a759293e64c350623dfc99fb9cebff985105bc87) - **settings**: allow editing the backend service address *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`72549c3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/72549c39bc737b6358bc759bef223cf4ab7ddddd) - **settings**: apply full debug settings to bundled backend startup *(commit by [@muyuzhong](https://github.com/muyuzhong))*

### Bug Fixes
- [`fd5e570`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/fd5e5700612de13d5a251382e788e18509558eb6) - **client**: reduce preview rendering overhead *(commit by [@tingfeng347](https://github.com/tingfeng347))*
- [`b51e38d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b51e38d87ed0b2bef2b2ad833654e5d794ce42ce) - **client**: exclude bundled Wayland libraries from AppImage *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`290809c`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/290809c8ccef0a1972089dbbb73796dad42bd234) - **server**: ground visual reviews and select representative frames *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`ec8f99f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ec8f99fac3ac338869f52f33c7230582460dcf39) - **server**: preflight model input budgets and record token usage *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`632b2a1`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/632b2a1a3ba7ef19b14fb4e4ff5d3ee9ae2bf9c4) - **server**: unify dotenv loading through a shared settings base *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`b830815`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b83081513fd2e26c9555cc9ee379d516781ac808) - **server**: disable cumulative model quotas by default *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`4b0ae22`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4b0ae22b5e412652f6efc9b85ddf7f8dbc4671eb) - **server**: reject unexplained visual review uncertainty *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`b47e708`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b47e708d3c6e996914a808f9dd2a9bff54e80a75) - **client**: correct AppImage media dependencies *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`ac7d833`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ac7d83316a6d95c7c2cc6905ece7012aba10dfc8) - **client**: stabilize Linux video preview and replay *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`92b835f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/92b835f69be4a103f99c656c6ab70feace5be965) - **client**: isolate Linux webview data by WebKit version *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`b925eb8`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b925eb8b6af9325bab87b5e00094c4e6b6d59a0a) - **server**: normalize judge requirement references *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`bd1d19e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/bd1d19e96d7f1acb31a3ef122452acf5a4c4a045) - **server**: steer compiler repairs without false progress *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`412aa31`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/412aa314345c0ce91d54c8fae3b9803d6f90abba) - **windows**: embed Common Controls manifests in test executables *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`7a65b2d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/7a65b2dbd5c855938da5feaf066bd84fc13baf3a) - **debug**: isolate backend startup and address security review *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`c33ac4e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c33ac4e5fd77f56a10c60d993d513cdcf8b6a0ef) - **settings**: lock local configuration read-modify-write operations *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`0c1c8bf`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/0c1c8bfdebef2e7facba8cc2cd7dbedf5ebff8c3) - **settings**: limit backend configuration to debug mode *(commit by [@muyuzhong](https://github.com/muyuzhong))*

### Refactors
- [`63844cf`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/63844cf4924a32e703f53a4357ba17ae76841737) - Improve point generation in visual_evidence.py *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`3f68d98`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3f68d9800bcac8d78d385b4561e7fe58c4acb532) - **settings**: simplify form state and regression tests *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`fe1e060`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/fe1e0605ff679f0944ff9e8f672d47b809a57d04) - **settings**: remove extra client credential checks *(commit by [@muyuzhong](https://github.com/muyuzhong))*

### Tests
- [`4811cbb`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4811cbb63c9a6b2f25444c92a1de10ada4a239ee) - **server**: simplify shared configuration regression coverage *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`7dfbec3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/7dfbec32f22755ebd5d9f428d5c68eacccd84e36) - **server**: stabilize composition concurrency regression *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`84d76f8`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/84d76f83c6e8ae31bdbf646f8313ce10a937eff4) - **server**: isolate composition timeout scenarios *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`a8ae802`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a8ae802805a0d3c3398054474df9b01b4ab8813f) - **client**: strengthen preview timeline coverage *(commit by [@tingfeng347](https://github.com/tingfeng347))*
- [`3c00298`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3c002989d6f7c2bd2d5b5a5bd4bef30952d0ae24) - **debug**: verify backend startup from packaged installers *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`85a3c8f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/85a3c8f735ffa96d6a9d5ec6b617933d878803ca) - **settings**: remove newly added dialog action tests *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`91c4ee3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/91c4ee379bb40f0a06bb1f49658858da26182ef9) - **settings**: trim redundant coverage for minimal workflow *(commit by [@muyuzhong](https://github.com/muyuzhong))*

### Build System
- [`ef0f537`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ef0f537bbde2b50eaf452df12e1adcc9415f986b) - verify AppImage libraries before uploading installers *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`7ae1aad`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/7ae1aad38f58f5fc94fc4b031321a0517a59cd7d) - **debug**: compile FFmpeg from official stable sources *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`3ae1b5d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3ae1b5d14616d0159f1113d85cce91d94ef0cc2d) - **debug**: assemble portable backend runtimes for all platforms *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`1e7a9f3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1e7a9f3017def3e3c7bdad84e71d51c4e76f0548) - **debug**: add automatic and manual desktop debug builds *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`c281af5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c281af540821a43fc90de8615c9b4e1a3622c502) - add optional backend integration validation *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*

### Documentation Changes
- [`c762b93`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c762b93cd83eb4d1a998045374cc31157a8cdd58) - update CHANGELOG.md for v0.4.0 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*
- [`da825ec`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/da825ec765aa7a24736d379744def8700a393745) - **server**: document review evidence and model budget boundaries *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`c33fd3a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c33fd3a462e590df4b175a60203374e1ddb65f3e) - **server**: document optional model quota enforcement *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`9586a04`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/9586a0491c35dfcb99230296a09ab17d689371df) - **remotion**: describe configuration and revision workflows *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`69b4289`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/69b428967166b6556f8d11a94d68cb74fd4e8d1c) - **debug**: document desktop bundles and CI usage *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`22aa851`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/22aa851e58ab48bb46b81108303d243c4e0318de) - **settings**: remove screenshot from source tree *(commit by [@muyuzhong](https://github.com/muyuzhong))*

### Code Style Changes
- [`52faf84`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/52faf848dadea10b88c3f009a12ca3c28ba22da9) - **client**: format local settings storage for CI *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`6bcd717`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6bcd717b44fd1e8d34891d4d9f5ee07c9cfdb6e6) - **settings**: remove trailing blank line in Rust tests *(commit by [@muyuzhong](https://github.com/muyuzhong))*

### Chores
- [`0d5a63c`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/0d5a63c848e50c1f5f406cff1152e70382e94a85) - merge upstream dev into fix/linux_bug *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`8c3ffb3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8c3ffb3095eba0e16e71e37504b478362e305a57) - merge dev and resolve configuration documentation conflict *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`ed87a98`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ed87a98c8dae107e0f07bd6a153bc7260141119f) - merge latest dev into client settings plugins *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`e7fccea`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e7fccead1d8644df4647936c53384808826d0109) - bump version into v0.4.1 *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

### Other Changes
- [`fcbc292`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/fcbc29211f1758aa073dd20377d8e041c606d5da) - Merge pull request [#44](https://github.com/HsiangNianian/IntelligentMixVideo/pull/44) from jyh20030112/fix/remotion-review-evidence

fix(server): ground Remotion reviews and bound model evidence inputs *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`59a7d5b`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/59a7d5b7f629f79dad94f7a7bf0ed821f87a5b26) - Merge pull request [#45](https://github.com/HsiangNianian/IntelligentMixVideo/pull/45) from muyuzhong/fix/server-config-base

fix(server): unify dotenv loading through a shared settings base *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`5096d1d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5096d1d50ffa5571eb76d5da6cdfddb18399ea03) - Merge branch 'main' into dev *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`55e3a7c`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/55e3a7c61ca17a339fb4b71cf4d58157749bffe9) - Merge pull request [#47](https://github.com/HsiangNianian/IntelligentMixVideo/pull/47) from jyh20030112/feat/remotion-parameter-drafts

feat(remotion): configure generation and save parameter drafts *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`fadc559`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/fadc55980b7e01a9c3c4faa5933fbddddb23e0e8) - Merge pull request [#43](https://github.com/HsiangNianian/IntelligentMixVideo/pull/43) from IT-coder-Yy/fix/linux_bug

fix(client): resolve Linux AppImage startup and preview failures *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`1eab344`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1eab344a8f1842e0fac41a4210e866d6774a5111) - Merge pull request [#38](https://github.com/HsiangNianian/IntelligentMixVideo/pull/38) from tingfeng347/fix/linux-preview-performance

fix(client): reduce preview rendering overhead *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`99f2dcd`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/99f2dcd1d76285847158ecc58d647cab0cce6c6b) - Merge pull request [#49](https://github.com/HsiangNianian/IntelligentMixVideo/pull/49) from jyh20030112/feat/remotion-workspace-versions

feat(client): redesign Remotion workspace with version history *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`f82d57d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f82d57de987dcf3178b1872acc4cf5159019f9e6) - Merge pull request [#48](https://github.com/HsiangNianian/IntelligentMixVideo/pull/48) from IT-coder-Yy/feat/debug_CI

feat(debug): bundle local backend runtimes for desktop testing *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`b311a35`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b311a35a85b405d0a8dd1bb24fc3c96ede04090e) - Merge pull request [#50](https://github.com/HsiangNianian/IntelligentMixVideo/pull/50) from muyuzhong/feat/client-settings-plugins

feat(settings): discover module settings and add local configuration dialog *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`d47ece2`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/d47ece2734c61defc03069ab3e768f0f110c3125) - Merge pull request [#46](https://github.com/HsiangNianian/IntelligentMixVideo/pull/46) from HsiangNianian/dev

fix(server): ground visual reviews and select representative frames *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`3d0f613`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3d0f61394d592cdf081a70902c6a195a245b5032) - Merge pull request [#51](https://github.com/HsiangNianian/IntelligentMixVideo/pull/51) from HsiangNianian/dev

chore: bump version into v0.4.1 *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

## [v0.4.0] - 2026-09-15
### New Features
- [`1a5774a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1a5774a0659ddf3cf9ce43d4ddb25d6f42d7ba53) - **server**: add task-local template contracts and model context *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`f51f67f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f51f67f4361013c089ccfab4da3920d5ddb1547b) - **server**: add verified Remotion template generation *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`4c2dc95`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4c2dc95016afcf86b18fbb1c1ed83ee916fb91db) - **server**: serve sealed Remotion previews and default exports *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`a86926e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a86926e9e6099aca4cd88a09ebc57609ba008635) - **client**: coordinate isolated Remotion template sessions *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`3e9b20c`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3e9b20cef23fce4c4ccc3170a0ad33690f2ce536) - **client**: add the Remotion chat and preview workspace *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`182e2d3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/182e2d35a153cf8a0abb5da6fffefd0894124c34) - **server**: persist public Remotion conversation history *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`94238e1`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/94238e16fb1307e6c1af63a68c033ef52088ddad) - **server**: stream replayable Remotion session events *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`4018583`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/40185831c5d2068aa850fc1533456b212fcf11b4) - **client**: add resumable Remotion SSE transport *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`ddf3700`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ddf3700f0fd1ac40520d6d0e9bf51aeb62d0e1ff) - **client**: restore Remotion conversations from history and SSE *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`00985de`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/00985de8f739ce66f77235ab5e385c3afbeb5c1e) - **remotion**: persist replayable task progress and answer history *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`45881d2`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/45881d2addf1ae426669a7d472360d62bae73374) - **remotion**: configure model budgets and phase accounting *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`2b02c62`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2b02c622acdc3ab615b37397248616f69015c8ad) - **client**: display reviewed answers and task progress timelines *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`2bbc32e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2bbc32e8262e59fa5c71872141a26ba340de96d3) - **server**: add composition contracts and timeline generation *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`0dba916`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/0dba9164072205ba28dba66922424ad1bb4a02b8) - **server**: integrate IMS and material matching clients *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`6aa46a1`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6aa46a1f00b983ab058132200730f6775a6bde0e) - **server**: expose persistent video composition jobs *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`5be864d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5be864dcfe8b77dfc1c460e6e75c5b8b263bbfab) - configure server port and default client API to 20070 *(commit by [@left0ver](https://github.com/left0ver))*

### Bug Fixes
- [`071bdb7`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/071bdb7dcaa9678053d03b211a2d5916143b77e3) - **server**: allow SSE replay headers from local clients *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`be2b8c5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/be2b8c5884f2104ddedca11057887a1c30b29cb7) - **remotion**: keep parameter history messages readable *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`828c534`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/828c5342e58dacc27e2975b4924208b7529e563b) - **client**: preserve reading position when loading older messages *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`b1ac026`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b1ac02680b8e067664e1d32a9287edfe91cc01f7) - **remotion**: tolerate bounded raster noise in rendered evidence *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`5540c38`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5540c384fd08658ac3474b00ddd89b171df0b4eb) - **client**: separate chat waiting from preview rendering *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`badcf4e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/badcf4eb39a7d7f3098b9289c953b4fd8f5b9405) - **client**: align Remotion API with the upstream server port *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`5d36d0d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5d36d0d2549af8bd98d6f4ff1e1557c99452d7cd) - **client**: preserve template library drafts across workspace tabs *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`49a87c6`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/49a87c6f926fd37789199372cc1d2da142a3c870) - **remotion**: recover artifact reads without blocking session events *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`866f631`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/866f631ecd6e823d67c50e25cf43669b31277f60) - **remotion**: guard recovery against pending parameter edits *(commit by [@jyh20030112](https://github.com/jyh20030112))*

### Refactors
- [`02d5a68`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/02d5a688b6e7e7b4762717f7d6c23930b5acdaf1) - **server**: rename template agent package to remotion_templates *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`49e0d16`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/49e0d166654191f9816348ca415f1be1a2ae6ae4) - **remotion**: unify actor planning and verified completion *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`a092000`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a0920001105264b2a2eced80504d9f083fad4ac3) - **remotion**: remove obsolete context paths and duplicate image code *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`b1b586f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b1b586fb315cab8d31e0867fcb6d1512c637f741) - **server**: register segmentation route inside its module *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`26060fe`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/26060fec263523adeaa0fd251b64c284606f7599) - **server**: move the segmentation contract into schema.py *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`d07a220`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/d07a220320a5bd41f7ee2461f9a293fc137ed50a) - **server**: separate API examples and remove example tests *(commit by [@muyuzhong](https://github.com/muyuzhong))*

### Tests
- [`a547794`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a5477947ae633ca8509947912ad8b2d2ccb7cd28) - **remotion**: isolate renderer process checks from host tools *(commit by [@jyh20030112](https://github.com/jyh20030112))*

### Documentation Changes
- [`84763ec`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/84763ecf783dd2ce754e0eec6d8a400fba72169f) - **server**: document template service setup and API *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`e7987ba`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e7987ba994b1b383aefaafa09654f94c88bdcdf9) - **server**: update Remotion service paths and module guidance *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`61c5e3a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/61c5e3a58d59ea901d42ec2c3682af2956d3d00c) - document Remotion workspace behavior and setup *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`23a2c08`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/23a2c0869029e65be3b09eb1be7202092e084d3d) - document Remotion conversation history and SSE recovery *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`10fe888`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/10fe88855c25c6b31551e87be975396296f99288) - update CHANGELOG.md for v0.3.2 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*
- [`8e4a24e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8e4a24e52b02b0d47c9467d317aed044947c9d4f) - **server**: name the previously untagged OpenAPI groups *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`a4ac8d8`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a4ac8d893f48934481d63b36bd99850e95a4dd75) - **server**: annotate alignment and time projection in segmentation *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`d213277`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/d21327742514d208ee66eb7267f27fcd79d2d2bf) - **server**: add step headers to segmentation flow *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`c2353ef`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c2353efa92bc418fd7540cd1442ee924080ff6ee) - **server**: exclude segmentation annotations from this PR *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`7da8010`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/7da801062d8a5e1e77cddbbb874f56a6f639b8f9) - revert guide changes for API examples *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`4153b14`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4153b14e6da86615c90a95984417055d995d23ea) - **remotion**: clarify server binding and deployment access *(commit by [@jyh20030112](https://github.com/jyh20030112))*

### Chores
- [`aede048`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/aede048c48dbeb549a84b872a6817e7980d73c59) - merge upstream main into template agent branch *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`d9e14e3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/d9e14e3526bff3776ef3aebc0ad9a2547745ec1e) - merge upstream main with Remotion feature compatibility *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`f8d0f43`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f8d0f43804d5626d8ee7c34b86134871a817e5aa) - 修改监听的host为0.0.0.0 *(commit by [@left0ver](https://github.com/left0ver))*
- [`2781d9f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2781d9f8a84c879460fd4d709b5363313683106f) - merge upstream dev with Remotion service compatibility *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`e275a93`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e275a93e87286fbfe77fb2e3acce8d34171a9a2e) - bump version into v0.4.0 *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`497e151`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/497e15184e8dbe786d2537ae5fafdca58416c6fb) - bump version into v0.4.0 *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

### Other Changes
- [`4975bff`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4975bff25b55bfb4978a933318fbf7ccc83495da) - Merge pull request [#39](https://github.com/HsiangNianian/IntelligentMixVideo/pull/39) from muyuzhong/refactor/segmentation-router

refactor(server): register segmentation route inside its module *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`f9cc28f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f9cc28f6252bb7f35123ce5efddaa166da39a55d) - Merge pull request [#42](https://github.com/HsiangNianian/IntelligentMixVideo/pull/42) from muyuzhong/refactor/api-docs-tags

docs(server): name OpenAPI groups and document segmentation examples *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`62644c8`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/62644c8494f1a62c4f987803e86a6309ca31ef58) - Merge pull request [#37](https://github.com/HsiangNianian/IntelligentMixVideo/pull/37) from IT-coder-Yy/feat/video_composition

feat(server): add asynchronous video composition API *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`b9d1fd9`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b9d1fd92a3f0eda30f470080bd9ed080f74d62c6) - Merge pull request [#41](https://github.com/HsiangNianian/IntelligentMixVideo/pull/41) from left0ver/feature/modify_default_port

feat: 支持服务端端口配置并将客户端默认 API 端口统一为 20070 *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`7e9bf89`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/7e9bf89c40f464ca0e7a3df9b5f7912faa50dc4c) - Merge pull request [#36](https://github.com/HsiangNianian/IntelligentMixVideo/pull/36) from jyh20030112/feat/server-template-agent

feat(remotion): add verified template generation and chat workspace *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`ef39ce3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ef39ce331f07a5a06ec20a230239179f69447314) - Revert "chore: bump version into v0.4.0"

This reverts commit e275a93e87286fbfe77fb2e3acce8d34171a9a2e. *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`f8cf954`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f8cf954080b7de104952fdd4b4ddcff74b1e5fe9) - Merge pull request [#40](https://github.com/HsiangNianian/IntelligentMixVideo/pull/40) from HsiangNianian/dev

refactor(server): register segmentation route inside its module *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

## [v0.3.2] - 2026-09-14
### Bug Fixes
- [`a01745e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a01745e7d35205b60f366a04d0b28d4525cd2193) - **ci**: bundle AppImage media codecs and verify decoding *(commit by [@muyuzhong](https://github.com/muyuzhong))*

### Tests
- [`b14e3da`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b14e3dae85c3d8449277ee1d9c55dada755dac01) - **ci**: require decoded AppImage media output *(commit by [@muyuzhong](https://github.com/muyuzhong))*

### Documentation Changes
- [`4848fb2`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4848fb2649f7e85a55c34a40e5007a931c1120fb) - update CHANGELOG.md for v0.3.1 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`79d8173`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/79d8173402e286663482f066418bdd86e6de5928) - keep documentation unchanged in codec fix *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`a30b04a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a30b04acd27773b30a67a7f1aec01cc18ef9c906) - **ci**: remove AppImage media smoke test *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`c01e316`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c01e3169c73bd6920f8388f522ccd3f7038bec87) - bump version into v0.3.2 *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

### Other Changes
- [`e7fd460`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e7fd4605c144f1d30ae1e1779ea339deafe44e0d) - Merge pull request [#33](https://github.com/HsiangNianian/IntelligentMixVideo/pull/33) from muyuzhong/fix/appimage-media-codecs-dev

fix(ci): include missing AppImage media codecs *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`a609bee`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a609bee025a1033095c81ac67449ae9169a718a4) - Merge pull request [#34](https://github.com/HsiangNianian/IntelligentMixVideo/pull/34) from HsiangNianian/dev

fix(ci): bundle AppImage media codecs and verify decoding *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

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

[v0.3.2]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.3.1...v0.3.2

[v0.4.0]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.3.2...v0.4.0

[v0.4.1]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.4.0...v0.4.1
