# Changelog

本项目的版本变更由发布工作流根据 Conventional Commits 自动生成。
推送正式版本 tag 后，成功发布的版本记录会自动写入此文件。

## [v0.9.18] - 2026-10-29
### New Features
- [`620d457`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/620d4575242e76ed58b670fd03a0494a4dfed248) - **remotion-agent**: add built-in text effects and allow clarifying questions *(PR [#283](https://github.com/HsiangNianian/IntelligentMixVideo/pull/283) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`ba64a83`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ba64a836af353fa033e08e530446306d00d2e8e6) - **video-composition**: limit transitions with dynamic spacing *(PR [#290](https://github.com/HsiangNianian/IntelligentMixVideo/pull/290) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*

### Bug Fixes
- [`8ce310d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8ce310dad96573be55f1cee83ae7557fb1ba34f3) - **remotion**: correct split reveal generation and validation *(PR [#276](https://github.com/HsiangNianian/IntelligentMixVideo/pull/276) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`b7b73bd`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b7b73bd6f035016c40e83bc36b0628f78da17541) - **remotion**: clarify keyword intent and effect scope *(PR [#280](https://github.com/HsiangNianian/IntelligentMixVideo/pull/280) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`98d3ee7`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/98d3ee7500335b6efb9ab1e4696b6be50d5bd6b0) - **remotion**: avoid false text fit failures *(PR [#282](https://github.com/HsiangNianian/IntelligentMixVideo/pull/282) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`93212af`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/93212aff2f416500e98fb9f6ead7fb0b95f30276) - **video-composition**: measure Remotion subtitle capacity *(PR [#287](https://github.com/HsiangNianian/IntelligentMixVideo/pull/287) by [@muyuzhong](https://github.com/muyuzhong))*
- [`0ee639c`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/0ee639c289342810d74c6d0a48dea3e535af2971) - remove frame masks through scene object controls *(PR [#288](https://github.com/HsiangNianian/IntelligentMixVideo/pull/288) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*

### Documentation Changes
- [`da97653`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/da9765346af79b3ecff0c9c1b599d9499967e6ba) - update CHANGELOG.md for v0.9.17 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*
- [`cd25443`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/cd254432408913bfd619459738b03855106f3620) - split AGENTS.md into per-directory guides *(PR [#277](https://github.com/HsiangNianian/IntelligentMixVideo/pull/277) by [@muyuzhong](https://github.com/muyuzhong))*

### Chores
- [`6b3fdab`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6b3fdab15779890564ce648ed47b3c10110d49ee) - **video-composition**: re-segment the playground sample by meaning *(PR [#284](https://github.com/HsiangNianian/IntelligentMixVideo/pull/284) by [@muyuzhong](https://github.com/muyuzhong))*
- [`c417906`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c417906541e45441b752aea854e76b4896ef4a59) - **release**: promote dev to main for v0.9.18 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`56863b6`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/56863b6629cc44d3028f200a2c7748853be482e5) - **release**: prepare v0.9.18 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`571a698`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/571a6985f6ab7483e21749e2bd0b9e589818bd35) - Merge pull request [#291](https://github.com/HsiangNianian/IntelligentMixVideo/pull/291) from YUZHEthefool/codex/release-v0.9.18

chore(release): promote dev to main for v0.9.18 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.17] - 2026-10-28
### New Features
- [`2b65f63`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2b65f63f55952eb14edb18e0cd79326bf5a73a32) - **remotion**: overwrite a saved Sprite asset by default *(PR [#262](https://github.com/HsiangNianian/IntelligentMixVideo/pull/262) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`734f9f7`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/734f9f7352ef0da1e62860508c579d49c9262079) - **client**: redesign the composition playground as a three-column workspace *(PR [#263](https://github.com/HsiangNianian/IntelligentMixVideo/pull/263) by [@muyuzhong](https://github.com/muyuzhong))*
- [`065d430`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/065d4305baa8da8cc27df8bb2f1bc164109b9a02) - apply transitions around subtitle keywords *(PR [#269](https://github.com/HsiangNianian/IntelligentMixVideo/pull/269) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`5bb220a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5bb220a5f8c5d222b26f5984679abff8494230d3) - **segmentation**: split subtitles by meaning and template capacity *(PR [#266](https://github.com/HsiangNianian/IntelligentMixVideo/pull/266) by [@muyuzhong](https://github.com/muyuzhong))*
- [`a4f15a8`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a4f15a8082a28e00635092dfe19ff4b7f90db1e4) - add avatar openings and configurable shared-screen layouts *(PR [#268](https://github.com/HsiangNianian/IntelligentMixVideo/pull/268) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`89f6eb3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/89f6eb34c1c64b98c56e1470d680ebffe9a6945e) - add configurable top and bottom frame masks *(PR [#270](https://github.com/HsiangNianian/IntelligentMixVideo/pull/270) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`f26f5bd`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f26f5bdf096990ca809ec5760dbb90b67770c46b) - **remotion**: give the Agent the primitives for complex text effects *(PR [#264](https://github.com/HsiangNianian/IntelligentMixVideo/pull/264) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Bug Fixes
- [`11bacdf`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/11bacdf3018178019f481c26f4399203a7b315ed) - **remotion**: keep wrapped bilingual subtitles apart *(PR [#265](https://github.com/HsiangNianian/IntelligentMixVideo/pull/265) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Documentation Changes
- [`24dc04f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/24dc04f57a8cd0d1d9db4db2b2dc8d755c1737bc) - update CHANGELOG.md for v0.9.16 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`e13fe8e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e13fe8eaa2c3648cc17f82773b451fbd3aff77de) - **release**: prepare v0.9.17 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`9978c1f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/9978c1f66c732fa366a634c65a5eb080e425210c) - **release**: promote dev to main for v0.9.17 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`f4e1fdf`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f4e1fdffaea6f723d5f31d35b7ef99b5c08bfac3) - Merge remote-tracking branch 'upstream/main' into codex/release-v0.9.17 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.16] - 2026-10-26
### BREAKING CHANGES
- due to [`5ffcd56`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5ffcd56a908df6a8cd6f0db5e023d8c574464e00) - return JSON from template endpoints *(PR [#247](https://github.com/HsiangNianian/IntelligentMixVideo/pull/247) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*:

  Template success responses now use application/json and Protobuf JSON field names. Callers must decode JSON instead of Protobuf binary.  
  * fix(client)!: decode JSON template responses  
  Request JSON and parse template responses through the shared Protobuf JSON schema. Update HTTP fixtures and editor regressions while retaining binary save requests.  
  The template client requires the server version that returns JSON success responses.  
  * docs(server): describe template JSON response contracts  
  * docs(client): clarify template request and response formats  
  * docs(api): document the template JSON migration  
  * fix(server): preserve legacy template response decoding  
  * fix(client): tolerate evolving template response formats  
  * docs(api): explain template response compatibility

- due to [`508671b`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/508671be008f734a50f6ee65d9654769e86012a1) - map keywords by ID and retry invalid outputs *(PR [#248](https://github.com/HsiangNianian/IntelligentMixVideo/pull/248) by [@muyuzhong](https://github.com/muyuzhong))*:

  ASR-backed keyword extraction requires a model endpoint  
  that supports strict JSON Schema with minItems/maxItems constraints.  
  Unsupported endpoints fail instead of falling back to JSON mode.  
  * docs: shorten segmentation collaboration notes  
  * fix(segmentation): use DeepSeek-compatible JSON output  
  * fix(segmentation): map keywords by segment ID  
  * fix(segmentation): bound correction attempts and timeouts  
  * fix(segmentation): cap requests and share a time budget  
  * fix(segmentation): redact detailed logs and use debug  
  * fix(segmentation): allow thirty seconds per model request  
  * fix(segmentation): address review feedback on correction retries  
  - Read missing or null choices, message, and content as an empty reply,  
    so boundary and keyword stages retry instead of raising AttributeError.  
  - Do not send an empty assistant turn when correcting an empty reply;  
    append only the correction message.  
  - Use a non-empty title_keyword placeholder in the keyword example.  
  - Let the default test stub accept title input, and cover corrected  
    boundaries and title keywords after a failed first attempt.  
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>  
  * docs(segmentation): condense segmentation notes  
  Shorten the segmentation sections of AGENTS.md and server/README.md and  
  state that invalid per-segment keywords are emptied without correction.  
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>  
  ---------


### New Features
- [`04eb9d3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/04eb9d3f2a4aa0211d15f24f1bca86403a126550) - **templates**: allow changing an AI effect's font in templates *(PR [#252](https://github.com/HsiangNianian/IntelligentMixVideo/pull/252) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`db94955`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/db94955d44c0dc815b6e547b36209d4f4a5622b9) - **client**: add a refresh control to the template preview *(PR [#255](https://github.com/HsiangNianian/IntelligentMixVideo/pull/255) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`e8fbfc4`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e8fbfc4a37c3d2e99d2b91fa4a2dc9a811398362) - add a persistent video composition playground *(PR [#258](https://github.com/HsiangNianian/IntelligentMixVideo/pull/258) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`2f53b00`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2f53b00bb7633d8b0b2772c616cbb47c596b2ec5) - **remotion**: overlap text tiers and keep transparency an effect *(PR [#259](https://github.com/HsiangNianian/IntelligentMixVideo/pull/259) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Bug Fixes
- [`13575a2`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/13575a23f0528cd5636a7a3d3ba95f8595d16da1) - **server**: keep a text tier's look on its keyword hits *(PR [#243](https://github.com/HsiangNianian/IntelligentMixVideo/pull/243) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`3780395`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3780395fe5d8778a16b535a6853d7db0837bb379) - **server**: log why a Sprite's files could not be deleted *(PR [#244](https://github.com/HsiangNianian/IntelligentMixVideo/pull/244) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`ee41a00`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ee41a000fdd97ea96e665b5542cbfdfb0eca5967) - **server**: keep ZOS files when records are deleted *(PR [#245](https://github.com/HsiangNianian/IntelligentMixVideo/pull/245) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`2f31e74`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2f31e747ebc5edb3953d3a826bc443c6def8bef3) - **remotion**: allow five-minute videos with 30-minute renders *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`4fbc203`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4fbc2038c84e801f85d55f4ae97fa76f8ce1256f) - **segmentation**: preserve raw keyword responses on failure *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`5ffcd56`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5ffcd56a908df6a8cd6f0db5e023d8c574464e00) - **templates**: return JSON from template endpoints *(PR [#247](https://github.com/HsiangNianian/IntelligentMixVideo/pull/247) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`508671b`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/508671be008f734a50f6ee65d9654769e86012a1) - **segmentation**: map keywords by ID and retry invalid outputs *(PR [#248](https://github.com/HsiangNianian/IntelligentMixVideo/pull/248) by [@muyuzhong](https://github.com/muyuzhong))*
- [`d0a15a5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/d0a15a52e237c2cebf22bb2909722840cbb7339b) - **client**: repair malformed fonts before upload *(PR [#257](https://github.com/HsiangNianian/IntelligentMixVideo/pull/257) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`417b25a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/417b25ab89ea064caf4f6de365a09b5b5dbe4f63) - **ci**: install Python 3.12 for the release preflight *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Performance Improvements
- [`492b866`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/492b8662ffeca93df52c9c4fec680565a18d2aef) - **remotion**: speed up rendering with bounded parallel chunks *(PR [#251](https://github.com/HsiangNianian/IntelligentMixVideo/pull/251) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*

### Documentation Changes
- [`6759824`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/67598242d4388ada5a2e73d4a856d4e1aec875c3) - update CHANGELOG.md for v0.9.15 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`1e2bcf4`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1e2bcf4258e4fa05e1713904739bbd018f0983c5) - **release**: prepare v0.9.16 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`d47d9d5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/d47d9d549979e02db75ffc1d8fbbd46633e9f2c6) - **release**: promote dev to main for v0.9.16 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`8e0c1a0`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8e0c1a00ad384eef1279602b1db39a213ad6bf91) - Merge pull request [#249](https://github.com/HsiangNianian/IntelligentMixVideo/pull/249) from IT-coder-Yy/fix/video-composition-duration-limit

fix(server): extend Remotion limits and log keyword failures *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`e3ba9b3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e3ba9b3a52a7c4c5026afe1b97ae14235fa2ef2a) - Merge remote-tracking branch 'origin/main' into codex/release-v0.9.16 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.15] - 2026-10-23
### Bug Fixes
- [`8c9564f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8c9564f0dc00e2e774ca6ae13148431afa9dcc74) - **client**: keep the template preview playable and editable when IMS fails *(PR [#238](https://github.com/HsiangNianian/IntelligentMixVideo/pull/238) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`f50739f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f50739f9c8bdd19a5fd798728a187023f622ec13) - **client**: play desktop preview videos over a loopback HTTP media server *(PR [#241](https://github.com/HsiangNianian/IntelligentMixVideo/pull/241) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Documentation Changes
- [`46047e9`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/46047e9e6ba166a347caf87147a605445db3d576) - update CHANGELOG.md for v0.9.14 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`2309ae6`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2309ae66ff7076847669d2e598f3184e323ebcb7) - **release**: prepare v0.9.15 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`c582d5d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c582d5d635f1b794e3a73bb87036eb2ae49c62dd) - Merge remote-tracking branch 'upstream/main' into codex/release-v0.9.15 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`b6f346a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b6f346a85249cf6b77b8abba89f0c1f886adefde) - Merge pull request [#242](https://github.com/HsiangNianian/IntelligentMixVideo/pull/242) from YUZHEthefool/codex/release-v0.9.15

chore(release): promote dev to main for v0.9.15 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.14] - 2026-10-22
### Documentation Changes
- [`4f1ef60`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4f1ef6091d399d07fb72fc362a6ff355ce4be80c) - update CHANGELOG.md for v0.9.13 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`eff3221`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/eff322120122743e23301bf5de2cec8951688a39) - **release**: prepare v0.9.14 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`2f846a7`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2f846a73af696524a2a4559b2bbe3af80bd1d748) - Merge pull request [#236](https://github.com/HsiangNianian/IntelligentMixVideo/pull/236) from YUZHEthefool/fix/template-preview-background-license

fix(client): keep template preview playback independent of the SDK license request *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`2a80382`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2a80382fe4fe81ebf9c8892b0aa46adbb66f75c2) - Merge branch 'main' into codex/release-v0.9.14 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`9f91dec`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/9f91dec3d7327f15ff55f796f130a4dd50d24bc2) - Merge pull request [#237](https://github.com/HsiangNianian/IntelligentMixVideo/pull/237) from YUZHEthefool/codex/release-v0.9.14

chore(release): promote dev to main for v0.9.14 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.13] - 2026-10-22
### New Features
- [`378e048`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/378e04867839e6752d986b738ab59fbd2a243968) - **client**: report playback smoothness and more in the debug log *(PR [#233](https://github.com/HsiangNianian/IntelligentMixVideo/pull/233) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Bug Fixes
- [`d4add6a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/d4add6a1643a959d3234127c25e8b2bc2fcef4ec) - **client**: stop WebView2 from opening a console window on Windows *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`dc69599`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/dc69599565aee8a9dbfeaf54c3dd383f1e566278) - **client**: keep Linux native diagnostics out of normal launches *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`ab5939e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ab5939e6c3c656589b916f16a5c5307432f230d6) - **client**: keep the template preview canvas at 1080x1920 *(PR [#234](https://github.com/HsiangNianian/IntelligentMixVideo/pull/234) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Documentation Changes
- [`776281b`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/776281baa368af4d612e1aeaeabf16916be1bb64) - update CHANGELOG.md for v0.9.12 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`6823d55`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6823d5581713de940faadd1b9a2f85baea9bc717) - **release**: prepare v0.9.13 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`66039f4`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/66039f492c0a6be54c43cbf3d4c379b4ff71639b) - Merge pull request [#232](https://github.com/HsiangNianian/IntelligentMixVideo/pull/232) from YUZHEthefool/fix/windows-webview2-console

fix(client): keep native diagnostics out of normal launches *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`2dbf5c3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2dbf5c36b70e3794a4ca061038d25ee45bcb61c8) - Merge branch 'dev' into codex/release-v0.9.13 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`75534e1`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/75534e10a5aa1efbeb73902de7ac5493bf3323d7) - Merge pull request [#235](https://github.com/HsiangNianian/IntelligentMixVideo/pull/235) from YUZHEthefool/codex/release-v0.9.13

chore(release): promote dev to main for v0.9.13 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.12] - 2026-10-22
### New Features
- [`6f8e54e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6f8e54e7756b0413e2d03eec7a1e35d41ab6d201) - **client**: diagnose template player failures and capture native logs *(PR [#230](https://github.com/HsiangNianian/IntelligentMixVideo/pull/230) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Bug Fixes
- [`a8847b4`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a8847b4e7e97bfc41506148afc4810004ebbf474) - recover font uploads stored after a client failure and keep CORS on 500s *(PR [#229](https://github.com/HsiangNianian/IntelligentMixVideo/pull/229) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Documentation Changes
- [`316624c`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/316624cd2aa20fa9bc175fb186e58648112fef4b) - update CHANGELOG.md for v0.9.11 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`7bee5cc`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/7bee5ccd713a9993e1491fa525ce03f307253348) - **release**: prepare v0.9.12 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`33af2d6`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/33af2d6f9a848dc856629fc049bd624344233267) - Merge remote-tracking branch 'upstream/main' into codex/release-v0.9.12 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`b15f8f4`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b15f8f426cd3f91b27d0435283f7880158f8df8e) - Merge pull request [#231](https://github.com/HsiangNianian/IntelligentMixVideo/pull/231) from YUZHEthefool/codex/release-v0.9.12

chore(release): promote dev to main for v0.9.12 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.11] - 2026-10-21
### BREAKING CHANGES
- due to [`815ea71`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/815ea711ac9fe7ae4f2fcd3c0d5943506131ebdf) - remove local templates and keep templates cloud-only *(PR [#223](https://github.com/HsiangNianian/IntelligentMixVideo/pull/223) by [@YUZHEthefool](https://github.com/YUZHEthefool))*:

  desktop local templates are no longer shown or edited.  
  The old data/template/templates.json and the $client.template_path  
  setting stay on disk untouched; templates still needed must be  
  recreated as cloud templates.  
  Refs #209  
  * docs: drop remaining local template library wording  
  The cloud-only change left sentences that still described local templates:  
  README and README.zh said local and cloud templates do not sync and that  
  both libraries share the same save checks, client/README said both use the  
  same font catalog and validation rules, and AGENTS.md still described local  
  detail reads, local list summaries and a local JSON corruption error.  
  Describe saving, reading and listing for the single cloud library only.  
  * docs(proto): describe template messages as cloud-only  
  The header comment still said the messages are shared by the cloud API and  
  local save requests. The local save path and its Rust prost output are gone,  
  so reword it and carry the comment into the generated TypeScript header  
  (regenerated with buf; the Python outputs are unchanged).  
  * test(client): cover reopening a legacy local template position  
  An update recovery position saved by an older client carries an environment  
  field and may point at a local template. The editor now ignores the field  
  and reads the ID from the cloud library; check that a not-found answer shows  
  the retry action, drops the stale position, and a retry restores the template.


### New Features
- [`281245b`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/281245b34369da173f6d25bf2b1a1dc24bd2a091) - **client**: remind users about available updates in a popup *(PR [#222](https://github.com/HsiangNianian/IntelligentMixVideo/pull/222) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
  - *addresses issue [#221](https://github.com/HsiangNianian/IntelligentMixVideo/issues/221) opened by [@YUZHEthefool](https://github.com/YUZHEthefool)*
- [`815ea71`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/815ea711ac9fe7ae4f2fcd3c0d5943506131ebdf) - **client**: remove local templates and keep templates cloud-only *(PR [#223](https://github.com/HsiangNianian/IntelligentMixVideo/pull/223) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`a676364`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a67636449ffb9daa10c018675782c8b7f508c55e) - **client**: open live UI diagnostics from settings; fix HiDPI preview rendering *(PR [#227](https://github.com/HsiangNianian/IntelligentMixVideo/pull/227) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Bug Fixes
- [`89ba2d2`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/89ba2d2a791bcc2e850ed69530988b1591f8b7a3) - **client**: keep Remotion overlays aligned with the fullscreen preview *(PR [#225](https://github.com/HsiangNianian/IntelligentMixVideo/pull/225) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`c832741`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c832741b3058b5ccf1dfc2a8dda714d4dee6a767) - make AI effect and AI asset deletion work with Aliyun OSS *(PR [#226](https://github.com/HsiangNianian/IntelligentMixVideo/pull/226) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Documentation Changes
- [`6a054f4`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6a054f4e3c23f556229a3a385bd815aec74bb365) - update CHANGELOG.md for v0.9.10 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`cea31fa`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/cea31fa20da05fba679bca6666335f3b871cc65d) - **release**: prepare v0.9.11 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`b50c1d3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b50c1d38aca5f8f40300a6c0fda2ec731a84b499) - Merge remote-tracking branch 'upstream/main' into codex/release-v0.9.11 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`2d5fc7f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2d5fc7f6e69ae2a0d0d90d607afc9ddc41da8b77) - Merge pull request [#228](https://github.com/HsiangNianian/IntelligentMixVideo/pull/228) from YUZHEthefool/codex/release-v0.9.11

chore(release): promote dev to main for v0.9.11 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.10] - 2026-10-20
### Bug Fixes
- [`4728a67`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4728a67559898d05d9cf64a052694b86f6d848d6) - **client**: keep the preview and font library working behind proxies and older servers *(PR [#219](https://github.com/HsiangNianian/IntelligentMixVideo/pull/219) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Documentation Changes
- [`5d7a055`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5d7a05575bc2d9a4e92545b83be1c7d9f0b3b475) - update CHANGELOG.md for v0.9.9 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`e3ce450`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e3ce450fffe455a36db3e0900cb67c00b9fa3903) - **release**: prepare v0.9.10 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`8ff4fdd`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8ff4fdd57c6f1e8f0e840aaeaa4e15ff803940ae) - Merge pull request [#220](https://github.com/HsiangNianian/IntelligentMixVideo/pull/220) from HsiangNianian/codex/release-v0.9.10

chore(release): promote dev to main for v0.9.10 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.9] - 2026-10-20
### New Features
- [`c55f5ee`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c55f5eebf4d14b04e3e770c3a892f96b1715ab65) - **fonts**: group a family's styles into one font card with details *(PR [#214](https://github.com/HsiangNianian/IntelligentMixVideo/pull/214) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`790733e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/790733e2773360e670692ed526a5c88c829c2edb) - **remotion**: split text effect motion into configurable effect Presets *(PR [#215](https://github.com/HsiangNianian/IntelligentMixVideo/pull/215) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Bug Fixes
- [`8b0b261`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8b0b26132f80e5b491e876ba3432dcb0321338c7) - **remotion**: edit only the requested lines of an accepted Preset *(PR [#206](https://github.com/HsiangNianian/IntelligentMixVideo/pull/206) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`0588389`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/05883899ec02e96a9e556ba0fe172f7a45bd9cbf) - **client**: persist browser effect chat history in IndexedDB *(PR [#207](https://github.com/HsiangNianian/IntelligentMixVideo/pull/207) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`a5917ea`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a5917ea1ab5638a7004c2f1e07fd9f811aa254ec) - **fonts**: name uploads from the font and dedupe them by file hash *(PR [#208](https://github.com/HsiangNianian/IntelligentMixVideo/pull/208) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`8771d0d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8771d0d97ef539b498491095cf2d53c1f96b0f7b) - **client**: reduce template preview stutter *(PR [#213](https://github.com/HsiangNianian/IntelligentMixVideo/pull/213) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`37d104b`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/37d104bb99aa28216a8c6f05adeab450ae6cc906) - **remotion**: keep preview controls visible in Tauri *(PR [#216](https://github.com/HsiangNianian/IntelligentMixVideo/pull/216) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Documentation Changes
- [`5ca2033`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5ca2033feff17fa2227866a2b81e9c6eb739bb3b) - update CHANGELOG.md for v0.9.8 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`685ac70`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/685ac701f85329d8313dd87da0a0b0a9120675c9) - **release**: prepare v0.9.9 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`a804da4`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a804da42b60226ad903fce197903ba5adf970dd9) - Merge pull request [#217](https://github.com/HsiangNianian/IntelligentMixVideo/pull/217) from HsiangNianian/codex/release-v0.9.9

chore(release): prepare v0.9.9 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`98f20b1`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/98f20b10820048c3e216e3339475b8e7683cb3ce) - Merge pull request [#218](https://github.com/HsiangNianian/IntelligentMixVideo/pull/218) from HsiangNianian/dev

chore(release): promote dev to main for v0.9.9 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.8] - 2026-10-19
### New Features
- [`6dbecdd`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6dbecdd6f8b8282c465f1324935b90a817cb9608) - **remotion**: compose text effects from role Presets *(PR [#199](https://github.com/HsiangNianian/IntelligentMixVideo/pull/199) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`8c801a3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8c801a3d079a018e251289b61db43659feaf86bf) - **remotion**: share local preview videos with the template editor *(PR [#200](https://github.com/HsiangNianian/IntelligentMixVideo/pull/200) by [@muyuzhong](https://github.com/muyuzhong))*
- [`25871ac`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/25871ac4b851e02748c243a114584e888e2ab292) - **client**: add redesign prototype sandbox *(PR [#196](https://github.com/HsiangNianian/IntelligentMixVideo/pull/196) by [@muyuzhong](https://github.com/muyuzhong))*

### Documentation Changes
- [`34702ba`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/34702baaeb3e87c7c285de76c6f2ba1323074ec7) - update CHANGELOG.md for v0.9.7 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`dff7996`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/dff79961044ce959eb689a5f3cc8e285caafa1ba) - **release**: prepare v0.9.8 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`5d20269`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5d20269c212ca56247f439a1bf0641a057421ccb) - Merge pull request [#203](https://github.com/HsiangNianian/IntelligentMixVideo/pull/203) from HsiangNianian/codex/release-v0.9.8

chore(release): prepare v0.9.8 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`620ba0d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/620ba0dac20ec40b82ede988bbac6eb72f5b8695) - Merge pull request [#204](https://github.com/HsiangNianian/IntelligentMixVideo/pull/204) from HsiangNianian/dev

chore(release): promote dev to main for v0.9.8 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.7] - 2026-10-19
### Bug Fixes
- [`fac89c9`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/fac89c9b0dc570904c5cd999d56fe05c7032970b) - **client**: install rustls crypto provider before update checks *(PR [#194](https://github.com/HsiangNianian/IntelligentMixVideo/pull/194) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Documentation Changes
- [`efad46f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/efad46f9f3c8446c8cf5eb0e252646fafdd31a74) - update CHANGELOG.md for v0.9.6 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`d9ead65`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/d9ead65a3d96797219f0889e59db2e3fe59b0228) - **release**: prepare v0.9.7 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`0c1b89e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/0c1b89e40609dc54ee4070ab474b83de844fabbf) - Merge pull request [#195](https://github.com/HsiangNianian/IntelligentMixVideo/pull/195) from HsiangNianian/dev

chore(release): promote dev to main for v0.9.7 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.6] - 2026-10-16
### New Features
- [`3ced7b9`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3ced7b9a410e62abffb4bd4669a141ab7366b071) - **templates**: support bilingual titles and subtitles *(PR [#185](https://github.com/HsiangNianian/IntelligentMixVideo/pull/185) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`22356ab`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/22356ab5c5473bb3405d138177999b3a191433a2) - **client**: add signed updates with compatible UI hot reload *(PR [#186](https://github.com/HsiangNianian/IntelligentMixVideo/pull/186) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*

### Bug Fixes
- [`ce8b09f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ce8b09fa80d04ed7ae5b849ef7a4789151db002b) - **client**: align @tauri-apps/api with the tauri 2.12 crate *(PR [#190](https://github.com/HsiangNianian/IntelligentMixVideo/pull/190) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`beec525`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/beec525fbe62880fa76211bb3b4faca52fdee522) - **release**: configure updater signing for tagged builds *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`6777a43`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6777a435851d199c4bb19a3f6da4e302212840b2) - **release**: build macOS updater app bundles *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Documentation Changes
- [`26773fa`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/26773fa249334abbd7a9475f91931e2c11933e11) - update CHANGELOG.md for v0.9.5 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`1f2ab70`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1f2ab70f465c828c93a9eeff2b4b25f1d7b1cd00) - **release**: prepare v0.9.6 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`632ed52`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/632ed522e4317b4952a5063eceee2ac8581979c5) - Merge pull request [#187](https://github.com/HsiangNianian/IntelligentMixVideo/pull/187) from HsiangNianian/feat/remotion-multiline-text

feat(remotion): support multi-line text effects revealed line by line *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`42e0ab9`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/42e0ab9fa63fb0055cb10c568eb649a7bf5f0205) - Merge pull request [#191](https://github.com/HsiangNianian/IntelligentMixVideo/pull/191) from HsiangNianian/dev

chore(release): promote dev to main for v0.9.6 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`f605972`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f60597277327718477d733f5360cff3680fc5733) - Merge pull request [#192](https://github.com/HsiangNianian/IntelligentMixVideo/pull/192) from HsiangNianian/dev

fix(release): configure updater signing for tagged builds *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`1c8edde`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1c8edde428b3b50c5f7db9910d44aba4b2d2d702) - Merge pull request [#193](https://github.com/HsiangNianian/IntelligentMixVideo/pull/193) from HsiangNianian/dev

fix(release): build macOS updater app bundles *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.5] - 2026-10-16
### New Features
- [`83792ce`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/83792cee88b9f5001adbf9fbdc816a14580c3c3f) - **client**: add template deletion and inline metadata editing *(PR [#177](https://github.com/HsiangNianian/IntelligentMixVideo/pull/177) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`9af20da`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/9af20da6eccc85c0a9f3dff1d79b5c2defbb4290) - **segmentation**: add English translation for segmentation results *(PR [#178](https://github.com/HsiangNianian/IntelligentMixVideo/pull/178) by [@muyuzhong](https://github.com/muyuzhong))*
- [`f0a1c3f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f0a1c3f345c458665226a125c838b980f8cfddb8) - **client**: let template previews use local video files *(PR [#181](https://github.com/HsiangNianian/IntelligentMixVideo/pull/181) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Documentation Changes
- [`eec7b80`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/eec7b80e5f7515d338c84bb76f66daafca14578f) - update CHANGELOG.md for v0.9.4 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`1343cb3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1343cb3cc204e988db1074f4d9b9ff9f04b2c2ca) - **release**: prepare v0.9.5 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`df3af8f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/df3af8f912dfc275c08fb5de4d7c2cef04772656) - Merge pull request [#182](https://github.com/HsiangNianian/IntelligentMixVideo/pull/182) from HsiangNianian/dev

chore(release): promote dev to main for v0.9.5 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.4] - 2026-10-15
### BREAKING CHANGES
- due to [`e045efd`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e045efdcaad4440cfc2d07dd5ce0d2f31dd4bbbf) - infer and persist modes from script fields *(PR [#167](https://github.com/HsiangNianian/IntelligentMixVideo/pull/167) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*:

  Use text for avatar narration and copy for media-only  
  subtitles. Both fields cannot be non-blank, and any non-blank script  
  requires audioUrl. videoUrl no longer selects the workflow.  
  * docs(composition): document script-based mode routing  
  Update the API guide, READMEs, and collaboration guide with the new  
  request rules, persisted mode location, and legacy recovery behavior.


### New Features
- [`b6d37d8`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b6d37d858251637961d4e0c80734198c44638a40) - **remotion**: bundle the preinstalled fonts with the backend package *(PR [#169](https://github.com/HsiangNianian/IntelligentMixVideo/pull/169) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`1ad9884`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1ad98844369632ae76d27f913cbece43c59cfe16) - **remotion**: show each tool call's arguments in the loop record *(PR [#174](https://github.com/HsiangNianian/IntelligentMixVideo/pull/174) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`f50bc72`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f50bc7287d7b692f9983e71be9253b424e2c770d) - **templates**: add title and subtitle font selection with previews *(PR [#172](https://github.com/HsiangNianian/IntelligentMixVideo/pull/172) by [@left0ver](https://github.com/left0ver))*

### Bug Fixes
- [`e045efd`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e045efdcaad4440cfc2d07dd5ce0d2f31dd4bbbf) - **composition**: infer and persist modes from script fields *(PR [#167](https://github.com/HsiangNianian/IntelligentMixVideo/pull/167) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`675b9b3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/675b9b37796d0e5655aaf85768bfbd2aada753c5) - **client**: simplify settings and close dialogs after saving *(PR [#168](https://github.com/HsiangNianian/IntelligentMixVideo/pull/168) by [@muyuzhong](https://github.com/muyuzhong))*

### Documentation Changes
- [`3440adf`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3440adf68f701bd15f4e88be57350f61cfce8863) - update CHANGELOG.md for v0.9.3 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`e35cb3d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e35cb3d69f111c4922c14bb323da5bb0fab29549) - **release**: prepare v0.9.4 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`8e2b6e3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8e2b6e33fbe514f5cdc89e0df0209096b8ee8790) - Merge pull request [#176](https://github.com/HsiangNianian/IntelligentMixVideo/pull/176) from HsiangNianian/dev

chore(release): promote dev to main for v0.9.4 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.3] - 2026-10-15
### New Features
- [`1ae37f1`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1ae37f14520d061b18119e41aac37aa6e32fb425) - **remotion**: add a shared font library that the agent chooses from *(PR [#165](https://github.com/HsiangNianian/IntelligentMixVideo/pull/165) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Documentation Changes
- [`713ded5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/713ded550e7fd54c2c1cf74298f590350319980f) - update CHANGELOG.md for v0.9.2 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`1953ac5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1953ac528ac9602953df1df28c98ad5952c121dd) - **release**: prepare v0.9.3 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`9b66f73`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/9b66f733d3a381b68a6f559fa8fa57f25dc14687) - **release**: promote dev to main for v0.9.3 *(PR [#166](https://github.com/HsiangNianian/IntelligentMixVideo/pull/166) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.2] - 2026-10-14
### BREAKING CHANGES
- due to [`3362018`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3362018027d4608c75979aca394ec50eb8c209a3) - keep chat on the client and execution state in MySQL *(PR [#143](https://github.com/HsiangNianian/IntelligentMixVideo/pull/143) by [@YUZHEthefool](https://github.com/YUZHEthefool))*:

  the server session snapshot, history pagination and job event  
  polling routes are removed, work routes require the X-IMV-Device header, and  
  clients and server must be upgraded together. Chats, versions and files in the  
  old backend SQLite are not migrated.  
  Closes #144  
  Refs #126  
  * test(server): check bounded shutdown without MySQL or the removed runtime lock  
  The graceful shutdown regression from #141 waited for  
  /api/templates/capabilities, which now builds the Remotion runtime and so  
  answers 503 without MySQL, and it still flocked runtime.lock, which the  
  backend no longer creates. Probe a test route instead and assert only the  
  bounded exit. Comments and docs that said the timeout releases the runtime  
  lock now say it stops the old process's job queue.  
  * fix(remotion): build the runtime once when first requests arrive together  
  runtime_for now awaits the MySQL initialization in a thread, so requests  
  arriving before it finished each built their own Runtime: overlay tasks  
  resumed and rendered several times, several retention sweeps ran, the  
  stores used separate write locks, and a later recovery could interrupt a  
  job an earlier runtime had just claimed. Concurrent first requests now wait  
  for one initialization under a per-app asyncio lock.  
  * fix(remotion): let clients catch up works they do not stream  
  Only the open work received events, so a work left generating in the  
  background never stored its reply locally, and after the undelivered-event  
  retention the reply and version pointer were deleted for good.  
  GET /works/{id}/events?after= returns one page of undelivered events in the  
  SSE shape for the client's background sync, acknowledged like streamed  
  events. The unused GET /works list route goes: the sidebar reads the local  
  store.  
  * fix(remotion): keep object store I/O off the event loop and the write lock  
  With ZOS configured, publishing a generated version uploaded every sealed  
  file (upload, HEAD and anonymous read each) synchronously on the event loop  
  inside the write transaction, freezing every request and stream; reference  
  images were downloaded on the loop, and deciding whether a parameter patch  
  is instant downloaded and hashed all sealed files, the preview MP4  
  included. Work deletion and the upload sweep called the object store while  
  holding the write lock.  
  - Publish uploads under the new version ID before the record transaction,  
    in a worker thread, and removes the upload when the commit fails unless  
    the version did commit (a lost acknowledgement no longer leaves a version  
    without files).  
  - Reference images are resolved in a worker thread.  
  - The instant check reads only interactive.js through the verified cache,  
    in a worker thread from the edit and retry routes.  
  - Work deletion removes objects before a short record transaction; the  
    upload sweep deletes records first and then the objects.  
  * fix(remotion): cache sealed version files one by one  
  Store.accepted downloaded a cold version into a staging folder, removed the  
  live folder and renamed the staging folder over it. Readers opening the  
  same version together (preview, diagnostics and downloads after a cache  
  wipe or on a new host) deleted the folder another reader had just returned  
  or failed the rename with ENOTEMPTY, answering 404 or 500. Each sealed file  
  is now cached through the verified per-file download with an atomic  
  replace before the whole version is checked against its evidence, and  
  publication seeds the cache the same way.  
  * fix(remotion): retry unfinished deletions in every retention sweep  
  expire_devices forgot the expired devices in the same transaction that  
  marked their works for deletion, and the sweep then deleted those works one  
  by one: one storage failure aborted the rest, and later sweeps never saw  
  the works again, so their records and public objects stayed until the  
  process restarted. A user DELETE answered 503 and never repeated stayed  
  pending the same way. The sweep now isolates each step and retries every  
  pending deletion each hour.  
  * fix(remotion): answer 503 when the object store is unreachable  
  ZOS and botocore failures are not OSError, so with ZOS configured a failed  
  work deletion answered 500 instead of the documented retryable 503, reading  
  a reference image or artifact answered 500, and an upload whose anonymous  
  read check failed came back as a 422 that read like an invalid image.  
  ZosObjects now raises ObjectStoreError, an OSError, for every failure  
  except a missing object, and the upload, reference image, artifact,  
  diagnostics and Sprite publish routes answer 503 for it.  
  * fix(remotion): rebuild a lost Sprite preview bundle once per asset  
  The anonymous preview route rebuilt a missing or mismatched bundle in a  
  sandboxed worker for every request: after this release every pre-existing  
  Sprite needs a rebuild, so opening the asset library started one worker per  
  preview and client, and a rebuild that kept failing could be triggered  
  without limit. Two rebuilds could also interleave their writes, and a  
  rebuild in progress re-uploaded the public bundle after DELETE removed it.  
  Previews and deletion of one asset now take turns under a per-asset lock,  
  waiting previews reuse the rebuilt copy, a failed rebuild answers 404  
  without a worker for five minutes, and an unreachable object store is a  
  503 rather than a reason to rebuild.  
  * fix(remotion): delete audit logs of pruned jobs with their work  
  Acknowledging a work's events deletes its earlier job rows, and work  
  deletion found audit logs by job ID, so the private logs (with prompts) of  
  pruned jobs outlived the deleted work by up to the audit retention period.  
  Audit logs now live in one folder per work, removed with the work; the  
  retention sweep expires files inside those folders and drops empty ones.  
  * fix(remotion): keep the live smoke run working on the shared database  
  The live acceptance script read the first result's frame from the job  
  folder, which is now removed when a job ends, so its image round trip  
  always failed. It also ran startup recovery against the shared MySQL,  
  interrupting the running jobs of a backend using the same database, and  
  left its works there forever under a device that never expires. It now  
  reads the sealed frame through the verified cache, only creates the  
  tables, and deletes its works when it ends.  
  * fix(remotion): record a device only when it creates a work  
  Every request with an unseen X-IMV-Device inserted a device row under the  
  global write lock and kept an in-memory entry until the 90-day expiry, so  
  random header values grew both without bound. A device row is now created  
  with the device's first work, a request from an unknown device writes  
  nothing, and the activity throttle keeps at most 4096 devices.  
  * fix(remotion): report a work deleted during enqueue as missing  
  Store.enqueue read the work's device with scalar_one before checking the  
  work inside its transaction, so a work whose deletion finished after the  
  route's check raised NoResultFound and answered 500. The transaction now  
  checks the work first and answers 404 or 410.  
  * fix(remotion): expire finished overlay tasks and the idle read cache  
  Overlay render rows were never removed, and each start loaded every row's  
  LONGTEXT to resume, so the table and startup cost grew forever. With ZOS,  
  the local read cache slowly mirrored every version, image and bundle ever  
  read. The hourly sweep now drops finished overlay tasks older than 30 days  
  that owe no callback, and removes cache files unused for 30 days; every  
  cache hit refreshes a file's time.  
  * test(remotion): exercise the work store on a real MySQL  
  Every Remotion store test used the SQLite stand-in, which hides MySQL  
  behaviour the store relies on: an UPDATE that changes nothing must still  
  count as an existing row (CLIENT_FOUND_ROWS, used by the conversation and  
  progress writes), the nullable one-active-job unique key, DATETIME(6)  
  cutoffs and acknowledgement pruning. The opt-in integration suite now runs  
  that path against the disposable MySQL of the Backend integration job.  
  * test(remotion): cover version isolation, ZOS writes and sweeps over Sprites  
  Add the regression coverage the audit found missing: another device reading  
  a version or its diagnostics gets 404 without starting a worker, ZosObjects  
  uploads through upload_public and deletes every listed page, and the  
  retention sweep that deletes an expired device's works keeps the Sprites  
  published from them.  
  * refactor(remotion): remove the unused Preset catalog export helper  
  catalog_record had no caller left once the Preset catalog became MySQL-only.  
  * docs(remotion): correct storage, retention and deployment notes  
  - State that nothing enforces the single backend instance and what a second  
    one on the same database would do, and that MySQL 8.0+ is required for  
    acknowledgement (AUTO_INCREMENT must survive restarts).  
  - Say that every sealed version file, the internal JSON included, is  
    public-read in ZOS and readable by anyone who knows the version ID.  
  - The retention sweep starts with the runtime on the first Remotion  
    request, not at server startup; list the tables created then.  
  - Publications can be deleted and their preview bundle is a rebuildable  
    object (sprite.proto, regenerated TypeScript).  
  - Rounds arrive as job.round events; the stream holds no SQLite  
    connection.  
  * refactor(client): drop the write-only local version index  
  The local session store wrote a `versions` row (and a `VersionEntry` in the  
  TypeScript change set) for every `version.ready` event, but nothing ever read  
  it: the version list and code come from the server through  
  `useTemplateVersions`. Remove the TypeScript type, the memory backend map, the  
  Rust `versions` table and the doc mentions; a ready version still moves the  
  work's `current_version_id` pointer.  
  * refactor(client): drop the dead history pagination  
  History now reads the whole local store, so `next_cursor` was always null:  
  the cursor merge branch and `more()` in `useWorkHistory`, the `WorkPage`  
  type, the workspace's `hasMore` and the sidebar's "加载更多会话" button could  
  never run. `history()` returns the summaries directly and the hook keeps a  
  plain list.  
  * fix(client): let the sidebar retry deleting works the server reports gone  
  When the open work's stream answered 404/410 the client cleared the view and  
  said "可在历史列表重试未完成的删除", but the local row kept its old job label  
  and nothing ever set the sidebar's `deleting` state (the server history list  
  that used to carry it is gone). Persist a `deleting` flag on the local work  
  when the server reports it missing or being deleted; the sidebar then shows  
  "已删除或正在清理 · 可重试删除", keeps the row from being reopened, and offers  
  "重试删除", which removes the local record once the server confirms.  
  * perf(client): run local session storage off the UI thread on one connection  
  `remotion_sessions` was a synchronous Tauri command, so every call ran on the  
  main thread, and each call reopened the SQLite file, switched it to WAL and  
  ran the CREATE statements again: three times per SSE event.  
  - The command is async and does its work in `spawn_blocking`; a managed  
    `Sessions` state holds one connection behind a Mutex, opened lazily on the  
    first call (a failed open is retried on the next call).  
  - The schema is created once on open and versioned with  
    `PRAGMA user_version`; a database written by a newer client is refused, and  
    version 0 (a new file, or tables left by unreleased test builds) rebuilds  
    the session tables while keeping the device ID.  
  - Each operation's payload is a fixed serde structure with  
    `deny_unknown_fields`, so a missing or extra field is reported instead of  
    being silently ignored. A shared fixture,  
    `client/tests/remotion-sessions-contract.json`, pins the TS/Rust contract:  
    the frontend test records the exact IPC payloads `localSessions.ts` sends and  
    compares them with it, and the Rust test parses and executes the same file.  
  * fix(client): scope local sessions by API server  
  The local store and `history()` ignored the API base, which users can change  
  in settings (`setApiBase`). After switching servers the sidebar listed the  
  other server's works; deleting one got 204 (the server treats an unknown work  
  as a no-op) and `local.forget` then erased the only copy of that chat.  
  Works, messages and jobs are now keyed by the normalized API base (origin plus  
  path, so case, default ports and trailing slashes do not split a server): the  
  Rust tables use (server, id) keys, every storage operation carries `server`,  
  and the memory backend keys its maps the same way. History, snapshots,  
  merges and deletion only see the current server's records; the device ID  
  stays global. The remembered-selection key is also computed per call instead  
  of once at module load.  
  * fix(client): stop and report local store failures instead of reconnecting forever  
  Tauri rejects with the Rust error string, not an `ApiError`, so a persistent  
  local SQLite failure (full disk, unreadable file) while writing an SSE event  
  fell into the generic catch: "连接中断，正在恢复更新" and a reconnect every two  
  seconds, forever, with the cause never shown.  
  The Tauri storage wraps command errors in `LocalStoreError`. When the watch  
  loop hits one it stops, clears the connection state and shows the reason with  
  "刷新任务"; the unacknowledged events stay on the server, so a refresh after  
  the problem is fixed resumes from the local cursor.  
  * fix(client): keep emoji titles whole and undo works the client cannot record  
  `record()` cut the sidebar title with `slice(0, 40)`, which can split a  
  surrogate pair. Tauri IPC (serde_json) rejects the lone surrogate, so  
  `local.record` failed after `api.create` had succeeded: the new work and its  
  running job existed only on the server, invisible locally, and resending the  
  message started a second generation.  
  - Cut the title by code points.  
  - If recording the new work still fails, `DELETE` it on the server (which  
    also cancels its job), then show the local error and put the text and images  
    back in the input like an upload failure. The message says whether the  
    server work could be undone.  
  * fix(client): silently drop a remembered work missing from the local store  
  In the browser the in-memory store is emptied by every reload while the  
  remembered selection in localStorage survives, so each reload greeted the  
  user with "会话已删除或正在清理…". A missing local row was reported as an  
  `ApiError(404)` and treated like a server-side deletion.  
  A missing local work now raises `MissingLocalWork`; restoring or watching such  
  a work clears the selection and returns to a blank chat without an error.  
  Real server 404/410 answers keep the deletion message and the retry path.  
  * fix(client): sync background works through the events route  
  Only the open work's SSE `watch()` applied and acknowledged events. A work  
  left running in the background kept showing "正在处理" in the sidebar, its  
  local job stayed `running` (so the input stayed locked when reopened late),  
  and once the server's 7-day undelivered-event sweep ran its reply and version  
  pointer were gone for good.  
  - `backgroundSync.ts` pulls, for every local work of the current server whose  
    latest job is queued or running and that the server has not reported  
    deleted, `GET /works/{id}/events?after=<cursor>` page by page until a page  
    is empty, applies each page with the same merge rules and acknowledges it.  
    The workspace runs it on mount and every 30 s while mounted and refreshes  
    the sidebar when anything changed; a 404/410 marks the work deleting, like  
    the open-work path. Network and local failures are retried next round.  
  - The work watched by SSE is skipped. To keep the two from ever applying the  
    same events, all local reads and writes of one work now run in a per-work  
    queue, and the sync re-checks "is it open now?" inside that queue right  
    before writing; a snapshot taken when a work is opened waits for an  
    in-flight background write, so the SSE resumes from the right cursor.  
  - The test protocol double serves the paged events route (two per page).  
  * fix(client): keep assets placed in the open draft from being deleted  
  The asset list let the user delete asset X while the open, unsaved template  
  draft had a clip using X. The server only refuses deleting assets placed in  
  saved cloud templates, so the delete succeeded and the next template save  
  failed with a misleading 422 "Remotion 资产尚未发布：<uuid>".  
  `SpriteCatalog` now receives the sprite IDs placed in the current draft  
  (including unsaved additions). Their delete button is disabled and the row  
  explains "当前模板正在使用，移除片段后才能删除" (linked with  
  aria-describedby); removing the clip enables it again.  
  * test(client): replace stale Remotion session checks with local-store ones  
  - `remotion.test.tsx` counted fetches ending in `/session`, a route that no  
    longer exists, so "retry does not re-read while a parameter draft is  
    pending" always compared 0 with 0. It now spies on `local.snapshot`, which  
    is what a read recovery calls.  
  - `remotion-layout.browser.mjs` (`bun run test:remotion-browser`) still mocked  
    the removed `/works?history=true` and `/session` routes and asserted every  
    request was a GET, which the `POST /ack` after each event breaks. It now  
    creates the work from a blank chat, delivers the history (four messages,  
    two jobs with loop rounds and their versions) through the SSE stream, allows  
    only the create, ack and delete writes, checks that a reload (which empties  
    the in-memory store) returns to a blank chat silently, and uses the current  
    "预览时长" picker labels. Passed locally against Vite with Chrome.  
  - The protocol double only answers `POST /works` now that the server has no  
    list route; it already deleted acknowledged events like the server.  
  - Doc comments for the small `reads`/`streams` test helpers.  
  * test(server): update the real MySQL template tests to the current contract  
  The opt-in MySQL integration tests only run in the manual Backend  
  integration workflow, so they kept assumptions the template API dropped  
  earlier: the list route returns TemplateSummary records without tracks  
  (since #109), text Sprite clips need a text_target (since #113), and the  
  fixture now holds both IMS text tracks. They still cover what nothing else  
  does (real bootstrap and utf8mb4, MySQL unique-key conflicts, rollback and  
  JSON columns), so compare list summaries, give the clips their text  
  targets and look tracks up by ID instead of removing them.  
  * fix(client): settle Remotion sessions stuck as running after their events expired  
  The server drops undelivered events after IMV_UNDELIVERED_EVENT_DAYS, so a  
  client that was offline for longer never receives the terminal event and  
  stayed locked in "running"; Stop could not recover it either. Read the  
  task state once when a session opens, on the first background sync after  
  mount and from the Stop receipt, and settle the local job and version  
  pointer. Later sync rounds still do not poll the task route.  
  * fix(remotion): keep a second backend off the queue and let idle streams sleep  
  Without runtime.lock nothing stopped a second backend on the same MySQL  
  from interrupting running jobs at startup. Add a lease row  
  (remotion_instance): acquired at startup, renewed every 10 s, released on  
  clean shutdown, taken over after 30 s; a refused instance answers 503, and  
  one that loses its lease stops claiming jobs and sweeping.  
  Idle SSE streams polled MySQL every 250 ms (~12 queries/s per stream).  
  They now sleep until a committed transaction appends an event of their  
  work, with a 5 s read as a safety net (~0.6 queries/s per stream).


### New Features
- [`7927653`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/7927653dd4e248e2bb3d226cddb4727f9f819630) - **remotion**: accept reference images on follow-up chat messages *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`afe7d4a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/afe7d4ade99e89f8f5d3c83f4ea6c49c6ef22110) - **remotion**: attach up to four reference images to a chat message *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`c206f79`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c206f791d43888d1e1961bb0c5989ce43fad59f1) - **client**: separate Remotion instance settings from asset defaults in template editing *(PR [#149](https://github.com/HsiangNianian/IntelligentMixVideo/pull/149) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`3a142b6`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3a142b65afb8f7f4e83398131983eefeace64138) - **templates**: redesign transition selection and timeline tracks *(PR [#145](https://github.com/HsiangNianian/IntelligentMixVideo/pull/145) by [@left0ver](https://github.com/left0ver))*
- [`fc2e60f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/fc2e60f37bb020a03d6b426271426d87ad5898c0) - **remotion**: rename Sprite assets and preview them with frozen frames *(PR [#98](https://github.com/HsiangNianian/IntelligentMixVideo/pull/98) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Bug Fixes
- [`2b464a7`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2b464a732f68edcb9402c5dcd84704f500e13788) - **remotion**: paste images and keep uploading after the chat starts *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`f230e64`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f230e6442a0a87c50e29a2810a737c3442576aba) - **remotion**: tell the model where each reference image came from *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`0c14f5f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/0c14f5f6c29ecc323c8e7bbd0c1f5dd612a1c085) - **remotion**: keep the draft when a reference image upload fails *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`813007d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/813007dce4aae098ce1053005eaaf3bb76744dae) - **remotion**: reclaim reference images no message ever used *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`cd1fc05`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/cd1fc0532d9f5620f40153166c43cd75d8e57670) - **remotion**: restore an undelivered draft in the same render as its error *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`a461099`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a4610999bcdb98de94ffc7fcf8c871eb838f6ab2) - **client**: bundle fcitx5 and ibus input methods in the Linux AppImage *(PR [#132](https://github.com/HsiangNianian/IntelligentMixVideo/pull/132) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`e543af2`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e543af26b200cf29a5e438aa2885fca2eb2a23c8) - **remotion**: keep erased variable annotations valid in composed Sprites *(PR [#139](https://github.com/HsiangNianian/IntelligentMixVideo/pull/139) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`edb0eac`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/edb0eac6d1d5e492429a07d2f98ff95329abdf55) - **server**: bound graceful shutdown with open SSE streams *(PR [#141](https://github.com/HsiangNianian/IntelligentMixVideo/pull/141) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`062e7c8`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/062e7c88c30ffb044c13071f97a18e72db188f75) - **remotion**: rebuild an outdated Sprite preview bundle on first preview *(PR [#146](https://github.com/HsiangNianian/IntelligentMixVideo/pull/146) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`f195615`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f195615185c51f5679b233561bdf011547336b78) - **remotion**: stop the composed instance box from clipping its content *(PR [#148](https://github.com/HsiangNianian/IntelligentMixVideo/pull/148) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`b2061bc`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b2061bc3022998d3755459313fb0958e3ac842e3) - **remotion**: publish Sprites even when boundary samples fail *(PR [#153](https://github.com/HsiangNianian/IntelligentMixVideo/pull/153) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`217a9d1`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/217a9d1f056683d329eb0b0e97ad55d2d939bf8a) - **client**: hide CS0003-000002 from effect selectors *(PR [#150](https://github.com/HsiangNianian/IntelligentMixVideo/pull/150) by [@left0ver](https://github.com/left0ver))*
- [`1104a98`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1104a984b93e85c8d858d9e6378d09af8112b353) - **server**: improve composition diagnostics and enforce text limits *(PR [#155](https://github.com/HsiangNianian/IntelligentMixVideo/pull/155) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`6f8b548`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6f8b548f4e6f7a7ad938dbcddc5d288db1d8fb8d) - **remotion**: keep fitted text inside the canvas and keyword sizes proportional *(PR [#159](https://github.com/HsiangNianian/IntelligentMixVideo/pull/159) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`3cf32a4`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3cf32a4fc225c0d1a22879e043792694762695a0) - **segmentation**: preserve words and numeric expressions *(PR [#160](https://github.com/HsiangNianian/IntelligentMixVideo/pull/160) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*

### Refactors
- [`3362018`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3362018027d4608c75979aca394ec50eb8c209a3) - **remotion**: keep chat on the client and execution state in MySQL *(PR [#143](https://github.com/HsiangNianian/IntelligentMixVideo/pull/143) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Tests
- [`fc2318e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/fc2318ecfea3eca1553474eb19906f881d6df433) - **client**: mark a cancelled animation's finished promise handled *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Documentation Changes
- [`e033f6f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e033f6feefebca76f515cb6bc941e3941a7012fc) - update CHANGELOG.md for v0.9.1 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`7e4c557`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/7e4c557210788d0a9b20cb6cd7bf6bdaed48c718) - **release**: prepare v0.9.2 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`8eda3cd`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8eda3cdff2d3663f12ffab742b62e0da9d39e7ba) - **release**: promote dev to main for v0.9.2 *(PR [#162](https://github.com/HsiangNianian/IntelligentMixVideo/pull/162) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`a35cc0f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a35cc0f70a6dcbae4c8c2e772445ed44c5f0625e) - Merge pull request [#131](https://github.com/HsiangNianian/IntelligentMixVideo/pull/131) from YUZHEthefool/fix/remotion-image-input-127

fix(remotion): paste images and keep uploading them after the chat starts *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.1] - 2026-10-14
### New Features
- [`2cbdce5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2cbdce535346588e772e9f26abcba546ecfee14d) - **composition**: integrate Remotion overlays into IMS timelines *(PR [#121](https://github.com/HsiangNianian/IntelligentMixVideo/pull/121) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*

### Bug Fixes
- [`6b0bae8`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6b0bae80d07087251330d2cccd57503415d2f6c2) - **remotion**: fit long text effect titles to the canvas instead of clipping them *(PR [#120](https://github.com/HsiangNianian/IntelligentMixVideo/pull/120) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Documentation Changes
- [`b0a4cd3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b0a4cd3a6f306eb8c2fb2a29deb99ca4738e5f63) - update CHANGELOG.md for v0.9.0 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`c69df93`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c69df93a38cc2aeaa7d1a3c9c0227c95a96f839f) - **release**: prepare v0.9.1 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`cb3f2b5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/cb3f2b548acaf827190509b8dc5210ef3c8a6b36) - **release**: promote dev to main for v0.9.1 *(PR [#124](https://github.com/HsiangNianian/IntelligentMixVideo/pull/124) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

## [v0.9.0] - 2026-10-13
### New Features
- [`eefbb84`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/eefbb84e31d1c7ee8eacf49b17120d5fb3a4cd1e) - **remotion**: render a template's Remotion clips into one overlay video for the bus *(PR [#97](https://github.com/HsiangNianian/IntelligentMixVideo/pull/97) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`9eacbd6`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/9eacbd6d0d604eb5313710278c165da728d69da8) - **video-composition**: use title keywords and deterministic splits *(PR [#99](https://github.com/HsiangNianian/IntelligentMixVideo/pull/99) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`d7d9107`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/d7d9107915468c50a58036a5f2626bfd60dd7675) - **remotion**: separate template parameters from Remotion code and split effects into categories *(PR [#106](https://github.com/HsiangNianian/IntelligentMixVideo/pull/106) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`69bcc95`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/69bcc952aa02e29cb01d840a96939cb4da5b0ccd) - **client**: choose the Remotion effect duration before generating *(PR [#107](https://github.com/HsiangNianian/IntelligentMixVideo/pull/107) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`71bf8f6`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/71bf8f664409956016acca4907c16052c4542d92) - **remotion**: align text keyword parameters with the bus keyword options *(PR [#108](https://github.com/HsiangNianian/IntelligentMixVideo/pull/108) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`75dc8ed`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/75dc8ed9fccabed92a5c86fb247d8894ceec0f0a) - **templates**: place Remotion clips in template object slots *(PR [#113](https://github.com/HsiangNianian/IntelligentMixVideo/pull/113) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`fd6485f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/fd6485fa4acb5638b8e4ada537f4a60e7817a3ba) - **remotion**: render Sprites with bus content at the bus's time *(PR [#115](https://github.com/HsiangNianian/IntelligentMixVideo/pull/115) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Bug Fixes
- [`1ed890b`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1ed890bdc6b9a5693ad2e9212da147f46eee904a) - **remotion**: surface a missing validation sandbox instead of a Preset store failure *(PR [#101](https://github.com/HsiangNianian/IntelligentMixVideo/pull/101) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`c8ac5ca`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c8ac5ca0e8eba0e099deba9b37cef666f9be28f2) - **remotion**: stabilise generation validation and agent step handoff *(PR [#102](https://github.com/HsiangNianian/IntelligentMixVideo/pull/102) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`326853e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/326853e0e409be6722cc3ec91428181ba0bce0a1) - **remotion**: make agent tool calls stable for weaker models *(PR [#104](https://github.com/HsiangNianian/IntelligentMixVideo/pull/104) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`4417648`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/441764851bc99723e42431617115b9277356392b) - **remotion**: recognise current player bundles by a protocol first line *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`c59586d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c59586d922c4225647a876dbc3dbd913197747cb) - **client**: accept every preview duration the server's frame contract allows *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Documentation Changes
- [`8dd4280`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8dd42804932b455f56edc3d2b55dd648c8a4d7f4) - update CHANGELOG.md for v0.5.0 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*
- [`ea75676`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ea75676621f87594dfadbba9151658fccfa04529) - **readme**: update readme *(commit by [@zjq-yl](https://github.com/zjq-yl))*
- [`bfc1e35`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/bfc1e354616d513cd9c8d612b285eb6834307fbc) - **readme**: add English README and preserve Chinese version *(commit by [@zjq-yl](https://github.com/zjq-yl))*

### Chores
- [`c954995`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c9549954f1ee7b5e9d42accaa355db1c6bdc9c3d) - **deps**: bump urllib3 in /server in the uv group across 1 directory *(commit by [@dependabot[bot]](https://github.com/apps/dependabot))*
- [`38f099a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/38f099afcfc15c2c88f8613cb338f2a93670226c) - **release**: prepare v0.9.0 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`1cbf644`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1cbf64493d49283d17948ddca61a01ee6f74473d) - **release**: promote dev to main for v0.9.0 *(PR [#111](https://github.com/HsiangNianian/IntelligentMixVideo/pull/111) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`30700ae`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/30700ae5e8592a1213f7d5236a24ebea3cb3a3cd) - feat(remotion)!: store clips as template tracks and Sprites in MySQL ([#103](https://github.com/HsiangNianian/IntelligentMixVideo/pull/103))

* feat(proto): add sprite_id to template tracks

Remotion clips will be stored as `remotion` tracks of a cloud template. Each
track keeps its own template-unique ID and needs a separate field that points
at the published Sprite, so the same Sprite can be placed more than once.

The field is a plain string where empty means unset, like `id` and `target`.
Document the remotion target, its seconds-only start, optional duration and
empty editor in the field comments.

Regenerate the Python, TypeScript and Rust code with `buf generate` and add the
new field to the Rust test fixture.

* feat(template): accept Remotion tracks

A template track can now be a Remotion clip: `target` is `remotion`, the new
`sprite_id` references a published Sprite, and the track `id` stays the unique
placement ID so one Sprite can be placed several times. Clips use a seconds
start, an optional duration (empty means the Sprite's own length) and an empty
editor, which is also what the API returns for them. Other tracks cannot carry
a `sprite_id` and keep exactly the JSON keys they had before.

The clip branch of the validator runs before the text and effect rules.
Without it an unknown target hit `getattr(editor, target)` and surfaced as an
HTTP 500 instead of a validation error. Clips add no effect IDs, so a template
may contain only clips, and they share the 100-track limit with IMS tracks.

The composition timeline skips clips: they are rendered as an overlay video,
not through IMS.

* feat(client): keep Remotion tracks when loading and saving

The server now returns Remotion clips as `remotion` tracks with an empty
editor. `toTemplate` rejected that editor as incomplete, and `listTemplates`
maps the whole list, so a single template with a clip made the whole cloud
library fail to load. A loaded clip would also have crashed the object list,
which has no icon for an unknown target.

Keep clips out of the IMS object list instead of widening `EffectTrack`: the
draft gets a separate `clips` array that is split off on load and appended as
`remotion` tracks on save, so editing other fields neither drops nor rewrites
them. Clips count towards the shared 100-track limit and allow a template
without IMS effects. Local templates do not support them, so saving a draft
with clips there is refused before it reaches the desktop.

Nothing in the editor creates or shows clips yet.

* feat(desktop): refuse Remotion tracks in local templates

Remotion clips reference Sprites that are published to the cloud backend, and
local templates never reach that backend, so they cannot hold clips. The save
conversion ignored the new `sprite_id` field, so a clip would only have failed
later on the generic track whitelist, while a stray `sprite_id` on an IMS
track would have been dropped silently.

Reject any `remotion` track or non-empty `sprite_id` up front with an explicit
message, before anything is written.

* docs: describe Remotion tracks in templates

Document the `remotion` track shape, its limits and how composition and the
client treat it, and the rollout caveat: servers and clients that do not know
the new track type fail on a template that contains one.

* test(template): cover Remotion tracks on a real MySQL

The SQLite stand-in used by the regular tests stores JSON as text, so it cannot
show how the MySQL JSON column returns a clip track. Add an opt-in case that
saves a template with clips, reads the stored configuration back and checks the
reference, the seconds start and the empty editor, and that IMS tracks still
carry no `sprite_id`.

* refactor(remotion)!: render overlays from request placements

The overlay endpoint took a style ID and read that style's clips from the
Remotion SQLite bindings, while the composition bus already holds the template
that names the same clips. Make the request carry them instead:
`placements: [{spriteId, start, duration?}]`, bottom layer first, at most 100.

The placements are stored with the task and rendered as stored. That also
removes a race of the old flow, where accepting and executing both read the
bindings, so a task recovered after a restart could render clips that were
edited in the meantime. Only the published Sprites are read again.

BREAKING CHANGE: `POST /api/v1/overlay-renders` no longer accepts `styleId`
and requires `placements`. The endpoint has not been released. Tasks queued
under the old request shape fail with `internal_error` after a restart.

* docs: describe the overlay placements request

Update the overlay section of the Remotion README and AGENTS.md: the request
carries ordered placements read from the template's `remotion` tracks, empty or
unpublished placements are a 422, and a recovered task renders the placements
stored with it.

* refactor(remotion)!: publish Sprites to MySQL

Published Sprites lived in the Remotion SQLite database, a file on one backend
host, while the templates that reference them are in MySQL. Store the
publication rows in a new MySQL table `remotion_sprites` next to the templates:
the JSON of the publication plus its source version, kind and text/keyword
fields as columns, unique together, so a repeated publish still returns the
original Sprite and a lost race resolves through the duplicate-key error.
The player bundle stays a local file under `sprites/<id>/`.

The catalog sorts in the application instead of in SQL. On a real MySQL the
`ORDER BY` over rows that carry whole JSON documents fails with "Out of sort
memory" once the documents get large. The two columns that hold JSON property
names are `utf8mb4_bin` because those names are case-sensitive, and
over-long names are refused with a 422 instead of a database error.

BREAKING CHANGE: Sprites published before this change are not read or migrated; publish their accepted versions again.
The old SQLite table is no longer created and an existing one is left alone.

* feat(template): require saved Remotion tracks to reference published Sprites

A `remotion` track points at a Sprite by ID, and both now live in the same
database, so a typo or a Sprite that was never published can be rejected when
the template is saved instead of failing later in the composition bus. Saving
a template whose clips name an unpublished Sprite returns a 422 that lists the
IDs and writes nothing; updates are checked the same way.

Sprites are only ever added, so a Sprite that exists at check time cannot
disappear before the write. Templates without clips never query the table and
do not create it.

Add a `seed_sprite` test helper that publishes a minimal Sprite row into the
database stand-in or a real MySQL, and make the earlier clip tests use it.

* docs: describe published Sprites in MySQL

Sprite publications are now rows of `remotion_sprites` next to the templates,
the preview bundle stays a local file, saving a template checks its clips
against the table, and Sprites published to the old SQLite table are neither
read nor migrated and have to be published again.

* feat(client)!: place Remotion assets as template tracks

Clips used to live outside the template: the editor kept them in a hook, and
saving a template was followed by a second request that replaced the style's
bindings under an optimistic lock. That made a save non-atomic (a template could
be saved while its clips failed), left the bindings behind when a template was
deleted, and made the bus look up the clips separately from the template.

The clips are now part of the draft and are saved with the template as
`remotion` tracks in one request, so the save either succeeds as a whole or
changes nothing. Adding an asset from the catalog creates an independent
placement, so the same asset can be added more than once; removing one or
dragging its start on the timeline edits the draft like any other object. The
unsaved-changes guard needs no second source of truth any more, and a template
that only has clips can be saved without a dummy IMS effect.

`useSpriteBindings` shrinks to `useSpriteCatalog`, which only reads the
published catalog, and the binding calls and the `Placement` type are removed
from the Sprite API. A clip whose asset is no longer in the catalog is flagged
so it can be removed before saving, because the server refuses unpublished
Sprites.

BREAKING CHANGE: the editor no longer reads or writes style bindings, so clips
placed with earlier versions are not shown and are not migrated; place them
again. It needs a server that accepts `remotion` tracks and checks their
Sprites.

* docs: describe Remotion clips as template tracks in the editor

Replace the description of separately saved style bindings with the editor
behaviour: clips are template tracks, saved atomically with the template, may
repeat an asset, are flagged when their asset is gone and are not supported by
local templates.

* refactor(remotion)!: remove style bindings

Where a Sprite is placed now lives in the template's `remotion` tracks, so the
separate store for it is dead: remove the `style_sprite_bindings` table, the
`GET/POST /api/sprites/styles/{style_id}` routes, the placement validation and
the optimistic revision lock, and the tests that pinned them. The OpenAPI route
set test no longer expects the removed path, and a test now asserts the old
routes answer 404.

BREAKING CHANGE: `/api/sprites/styles/{style_id}` is gone and the bindings
saved in `style_sprite_bindings` are neither read nor migrated; an existing
table is left in place. Place the clips again in the template editor.

* refactor(proto)!: remove the style binding messages

Delete `SpriteTarget`, `SpritePlacement`, `StyleSpriteBindings`,
`GetStyleSpritesResponse` and `SaveStyleSpritesRequest`/`Response`. Nothing
sends or reads them any more: placements are `remotion` tracks of
`imv.template.v1.EffectTrack`, which reference a published Sprite by
`sprite_id`.

The comments that still spoke of bindings now describe template tracks, and the
header points at the track that references a Sprite. `SpriteRenderInput` and
`SpriteParameterOverride` stay as they were: they belong to the unimplemented
render protocol and do not depend on the removed messages.

Regenerated with `buf generate`. `buf breaking --against v0.5.0` reports
exactly these six deletions.

BREAKING CHANGE: the removed messages are no longer part of `imv.sprite.v1`.

* docs: remove the style binding routes from the docs

State that placements are template tracks and that the binding table and routes
of earlier versions are gone, with their data neither read nor migrated.

* fix(template): reject Remotion tracks whose Sprites differ in canvas

An overlay render stacks all clips of a template into one video and requires
them to share width, height and fps, but saving a template only checked that
the referenced Sprites were published. A template with mismatched clips was
saved fine and the bus only found out with `canvas_mismatch` when it asked for
the overlay.

Read the canvas of the referenced Sprites in the same lookup that checks they
exist and answer a 422 that lists the canvases in play, on create and update.
`sprite_store.missing` becomes `canvases`, which returns the canvas of each
published ID.

Also cover the clips-only template over HTTP, which the existing test named for
it did not: it sent an empty `tracks` list. The new case creates, reads, lists
and updates a template with a published Sprite, one clip and no effects.

* fix(client): refuse Remotion assets with a different canvas

The server now rejects templates whose clips differ in canvas, but the editor
let such assets be added and only failed on save. Carry the published canvas on
the asset, reject an asset whose width, height or fps differs from the clips
already added (naming both canvases), and show each asset's canvas in the
catalog so the choice is visible before adding.

* docs: state the Remotion clip canvas rule

Clips of one template must share width, height and fps; the save checks it and
the editor refuses a mismatching asset. *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`af588ef`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/af588ef86858b40a18a42534156841ce30591f91) - Merge pull request [#110](https://github.com/HsiangNianian/IntelligentMixVideo/pull/110) from zjq-yl/docs/update-readme

docs: reorganize and expand bilingual README guides *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`a15c0c7`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a15c0c79d2d5aaab860f05f6d350ce431729a45d) - Merge pull request [#92](https://github.com/HsiangNianian/IntelligentMixVideo/pull/92) from HsiangNianian/dependabot/uv/server/uv-2a4a739b48

chore(deps): bump urllib3 from 2.7.0 to 2.8.0 in /server in the uv group across 1 directory *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`3c2eaca`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3c2eaca25c0767faae4b9cfc7c5e515abbad2294) - feat(templates)!: redesign template editor ([#109](https://github.com/HsiangNianian/IntelligentMixVideo/pull/109))

* feat(templates)!: enforce track limits and transition boundaries

Persist two transition boundaries through Protobuf and local storage. Require one title and one full-length subtitle when saving, allow at most one VFX track, and retain support for multiple filters.

Apply saved transition durations at their selected material boundaries and preserve template timing and layer order during composition.

BREAKING CHANGE: Templates must contain exactly one title and one subtitle to be saved. Existing templates with duplicate title, subtitle, or VFX tracks, or custom subtitle timing, must be updated to satisfy these rules.

Refs: [#105](https://github.com/HsiangNianian/IntelligentMixVideo/pull/105)

* feat(client): add configurable three-video template previews

Preview the first ten seconds of three configurable sources, reorder videos on the timeline, and assign transitions to consecutive boundaries in insertion order.

Load all three URLs through a dialog and support separate environment defaults. Keep effect timing in the inspector, fix subtitle coverage, enforce object limits before saving, and display asset errors in a dialog.

Document the editing rules and cover configuration, timing, ordering, validation, and workspace interactions with tests.

Refs: [#105](https://github.com/HsiangNianian/IntelligentMixVideo/pull/105)

* test(video-composition): cover template timing and layer rules

Assert title timing and title-before-subtitle ordering throughout composition API and recovery tests. Include the required subtitle when updating the template snapshot fixture.

Refs: [#105](https://github.com/HsiangNianian/IntelligentMixVideo/pull/105)

* refactor(client)!: number all preview video environment variables

Use VITE_PREVIEW_VIDEO_1_URL consistently in preview loading, types, test setup, examples, and documentation.

BREAKING CHANGE: Configure the first preview source with VITE_PREVIEW_VIDEO_1_URL and restart Vite or rebuild the client.

Refs: [#105](https://github.com/HsiangNianian/IntelligentMixVideo/pull/105)

* fix(templates)!: unify rule resolution and isolate invalid records

Resolve preview and composition timing from template rules and media context.
Read actual metadata for all preview sources and share resolved ranges across
the player, timeline, and inspectors while preserving fixed composition timing.

Validate stored records individually, return template summaries with validation
errors, and allow invalid records to be deleted without blocking valid templates.
Add shared timing cases, storage regression tests, and updated documentation.

BREAKING CHANGE: Transition tracks require transition_boundary and omit start
and start_mode. Template list responses now contain TemplateSummary records.
Upgrade clients and servers together. Incompatible stored templates must be
explicitly converted or recreated; records are not migrated automatically.

Refs: [#105](https://github.com/HsiangNianian/IntelligentMixVideo/pull/105) *(commit by [@left0ver](https://github.com/left0ver))*
- [`65942eb`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/65942ebb1068d83ef44d189f1305199a23962df6) - Merge pull request [#117](https://github.com/HsiangNianian/IntelligentMixVideo/pull/117) from YUZHEthefool/fix/pr111-review

fix: address Copilot findings on the dev→main PR [#111](https://github.com/HsiangNianian/IntelligentMixVideo/pull/111) *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*

## [v0.5.0] - 2026-10-10
### BREAKING CHANGES
- due to [`6cfc094`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6cfc0949e58644e45796ed15b82d4bd4fa209a54) - write composition logs to files asynchronously *(PR [#79](https://github.com/HsiangNianian/IntelligentMixVideo/pull/79) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*:

  video_composition_logs replaces detail with seven JSON columns. Stop older workers before starting the new server to migrate existing logs. Log consumers must follow the new fields and local references; ZOS outputs retain video links only.  
  * docs(server): document module logs and reference semantics  
  * chore: ignore local composition log directories  
  * build(server): exclude runtime logs from distribution archives  
  * refactor(server)!: persist execution logs in module JSON files  
  Replace database log storage and migration with seven module files. Keep business task transactions independent from logging failures and evict only finished task directories when the total log directory exceeds 50 MiB.  
  Execution logs are no longer stored or migrated in database tables. Consumers must read the per-task module JSON files; existing database log tables remain untouched.  
  * test(server): cover file log retention and storage failures  
  * perf(server): write composition logs through a bounded queue  
  Snapshot event payloads and timestamps before queueing. Use one writer thread, protect pending logs during cleanup, and drain the queue before closing the database.  
  * test(server): cover asynchronous log queue lifecycle  
  * test(server): allow ZOS responses while log writes are blocked  
  * docs(server): document asynchronous file logs and retention  
  * docs: update the composition logging overview  
  * docs: align project guidance with asynchronous file logs  
  * fix(server): preserve notification retry diagnostics

- due to [`6edeb13`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6edeb132850ef1704fc96463623a4e997e497bfa) - support voiced and silent material composition *(PR [#91](https://github.com/HsiangNianian/IntelligentMixVideo/pull/91) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*:

  Standard requests that omit materials now skip asset  
  matching and use only the avatar video. Send materials: [] to retain  
  unrestricted matching, or a nonempty array to restrict candidates.  
  * fix(server): accept null background music in all modes  
  * fix(server): place composition logs under the server project  
  * docs(server): document material composition contracts  
  * fix(server): reject non-numeric composition durations  
  * docs(server): clarify composition duration input types  
  * fix(server): read material video duration tags  
  * fix(server): treat blank matching URLs as unset  
  * test(server): cover avatar output and task recovery  
  * docs(server): clarify material probing and log paths  
  * fix(server): support language-qualified video duration tags  
  * docs(server): document language-qualified duration tags


### New Features
- [`e72566c`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e72566c34b3f5904b8588fbe1eae1f9b01c825c5) - **server**: split long segments at semantic boundaries *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`9b9abfa`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/9b9abfab53315458327fa245f7af3dd65e7609b5) - **remotion**: add LSP diagnostics and code highlighting *(PR [#83](https://github.com/HsiangNianian/IntelligentMixVideo/pull/83) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`6810537`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6810537e43c3cc2f5268a768920aa8d6913f2c5c) - **remotion**: record what each loop round actually did *(PR [#84](https://github.com/HsiangNianian/IntelligentMixVideo/pull/84) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`8a17fbe`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8a17fbe7fb95dc4d8ec80efb4edadaeadf0425b8) - **client**: redesign UI with light/dark themes and ObsidianUI components *(PR [#82](https://github.com/HsiangNianian/IntelligentMixVideo/pull/82) by [@muyuzhong](https://github.com/muyuzhong))*
- [`c2b3949`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c2b3949c7dd1e10acc22c2558fe097d0b3a2902f) - **client**: configure the local template storage path *(PR [#86](https://github.com/HsiangNianian/IntelligentMixVideo/pull/86) by [@muyuzhong](https://github.com/muyuzhong))*
- [`6e69dc5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6e69dc53bb034bebee0492968356b2e50d132af8) - **segmentation**: extract title keywords and retry invalid splits *(PR [#90](https://github.com/HsiangNianian/IntelligentMixVideo/pull/90) by [@muyuzhong](https://github.com/muyuzhong))*
- [`8273b41`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8273b41f73ebc5129eb6f12a1f6f4908a07ebed0) - **remotion**: publish accepted versions as standalone assets *(PR [#93](https://github.com/HsiangNianian/IntelligentMixVideo/pull/93) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`74c90a9`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/74c90a980545ce810b46553ce716afbc9d03ab87) - **remotion**: add independent Remotion assets to template editing *(PR [#94](https://github.com/HsiangNianian/IntelligentMixVideo/pull/94) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`6edeb13`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6edeb132850ef1704fc96463623a4e997e497bfa) - **server**: support voiced and silent material composition *(PR [#91](https://github.com/HsiangNianian/IntelligentMixVideo/pull/91) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*

### Bug Fixes
- [`2c45eab`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2c45eabf891dc28a04b4ceb3340832be43c1a040) - **server**: leave subtitle punctuation handling downstream *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`43645d1`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/43645d1b01e66768d1a33a538e246c708c35c740) - **remotion**: repair tool inspection and model request recovery *(PR [#85](https://github.com/HsiangNianian/IntelligentMixVideo/pull/85) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`5e35fb7`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5e35fb771eff5eb30c227f7be31c5cca9560697c) - **remotion**: stabilise tool calls and bound empty handoff loops *(PR [#88](https://github.com/HsiangNianian/IntelligentMixVideo/pull/88) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Refactors
- [`4b18292`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4b18292f577f0e4671dace1e170c342ff73b7187) - **server**: share segmentation error mapping *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`6cfc094`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6cfc0949e58644e45796ed15b82d4bd4fa209a54) - **server**: write composition logs to files asynchronously *(PR [#79](https://github.com/HsiangNianian/IntelligentMixVideo/pull/79) by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`5989192`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/59891929b338daa87157eccb810d127d84251f17) - **remotion**: deliver the Preset to Sprite agent flow *(PR [#80](https://github.com/HsiangNianian/IntelligentMixVideo/pull/80) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Tests
- [`f73feb2`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/f73feb2461e3f3332e16fbd59d50092d59cafc6d) - **server**: align segmentation fixtures with secondary splitting *(commit by [@muyuzhong](https://github.com/muyuzhong))*

### Documentation Changes
- [`424e4d7`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/424e4d77b340de28226c33a1a7cd4d2b32f36c41) - update CHANGELOG.md for v0.4.2 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*

### Chores
- [`ae2c519`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ae2c519448f895351db5ce9bff405e0c87843630) - **release**: prepare v0.5.0 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`07adeb6`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/07adeb64aac5eabfeaf087769ba7a99f16711e93) - **release**: promote dev to main for v0.5.0 *(PR [#96](https://github.com/HsiangNianian/IntelligentMixVideo/pull/96) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`a12e043`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a12e043fd0e3666c2a2ed95ba942b96cf654b797) - [ImgBot] Optimize images

*Total -- 335.51kb -> 308.34kb (8.1%)

/client/src/features/remotion_templates/screenshots/chat-deletion-desktop.png -- 117.24kb -> 105.90kb (9.67%)
/client/src/features/remotion_templates/screenshots/parameter-drafts-mobile.png -- 52.19kb -> 48.40kb (7.27%)
/client/src/features/remotion_templates/screenshots/parameter-drafts-desktop.png -- 166.07kb -> 154.04kb (7.25%)

Signed-off-by: ImgBotApp <ImgBotHelp@gmail.com> *(commit by [@ImgBotApp](https://github.com/ImgBotApp))*
- [`5583d8a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5583d8a7f1ba01a3f562771b1dfaddbcfc43cb86) - Merge pull request [#77](https://github.com/HsiangNianian/IntelligentMixVideo/pull/77) from muyuzhong/feat/segmentation-secondary-split

feat(server): split long segments at semantic boundaries *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`4cddafa`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4cddafa136241ad83edb3c96cd7ddfae54739bc4) - Merge pull request [#66](https://github.com/HsiangNianian/IntelligentMixVideo/pull/66) from HsiangNianian/imgbot

[ImgBot] Optimize images *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`0916c45`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/0916c4568ae7e98dcb5fce611c08e7e522f28969) - feat(server)!: support avatar-only composition without materials ([#87](https://github.com/HsiangNianian/IntelligentMixVideo/pull/87))

* refactor(server)!: store composition logs in module columns

Write new events directly into seven module columns, deduplicate payloads with local references, and preserve verified backups during legacy migration. Cover direct event writes, callback placement, video links, and migration behavior.

BREAKING CHANGE: video_composition_logs replaces detail with seven JSON columns. Stop older workers before starting the new server to migrate existing logs. Log consumers must follow the new fields and local references; ZOS outputs retain video links only.

* docs(server): document module logs and reference semantics

* chore: ignore local composition log directories

* build(server): exclude runtime logs from distribution archives

* refactor(server)!: persist execution logs in module JSON files

Replace database log storage and migration with seven module files. Keep business task transactions independent from logging failures and evict only finished task directories when the total log directory exceeds 50 MiB.

BREAKING CHANGE: Execution logs are no longer stored or migrated in database tables. Consumers must read the per-task module JSON files; existing database log tables remain untouched.

* test(server): cover file log retention and storage failures

* perf(server): write composition logs through a bounded queue

Snapshot event payloads and timestamps before queueing. Use one writer thread, protect pending logs during cleanup, and drain the queue before closing the database.

* test(server): cover asynchronous log queue lifecycle

* test(server): allow ZOS responses while log writes are blocked

* docs(server): document asynchronous file logs and retention

* docs: update the composition logging overview

* docs: align project guidance with asynchronous file logs

* fix(server): preserve notification retry diagnostics

* fix(remotion): resolve inspected tools by the name the model is shown

The model sees underscored function names (`preset_create`), but tools.inspect
looked its argument up as an exact dotted registry key, so inspecting any name
the model could actually see failed with TOOL_NOT_FOUND. In one recorded run
the Executor spent four failed inspections and four turns narrating the
failure, then reported the step as blocked.

Resolve the descriptor by dotted ID or by the wire name, and report the
registered IDs when the name is unknown, so a wrong guess is corrected in one
turn instead of being retried blind. Inspection stays contract-only: it never
executes a handler and never reveals an unregistered tool.

* fix(remotion): document the plan input_refs the host accepts

The accepted reference forms only appeared in the rejection message, so the
model had to build a plan, be rejected and rebuild it; one recorded run spent
two plan calls on that. Describe the accepted forms on the field the model
receives and in the Plan layer rules.

* fix(remotion): wait and retry transient model request failures

A single connection error ended the whole task: both failures recorded on
2026-10-09 were "Model endpoint is unreachable." after one attempt, throwing
away every earlier round of a generation that was otherwise progressing.

Retry connection failures, request timeouts and HTTP 429/5xx with an
exponentially growing wait (IMV_MODEL_RETRIES, IMV_MODEL_RETRY_DELAY_SECONDS,
capped at 30 seconds), record each retry in the private audit, and count the
logical call once. Configuration errors such as 401/403/404 still fail
immediately, a broken stream keeps its own meaning because it includes
deterministic size limits, and the wait is an ordinary sleep so cancelling
the job stops it at once.

* fix(remotion): expose complete host plan inspection contracts

* fix(remotion): preserve protocol and stream retry boundaries

* feat(server)!: skip matching when materials is omitted

BREAKING CHANGE: Requests without materials now produce avatar-only videos. Send an explicit empty array to retain material matching.

* docs(server): document avatar-only composition requests

* test(server): assert composition subtitle content

---------

Co-authored-by: thefool <thefoolyuzhe@gmail.com> *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*

## [v0.4.2] - 2026-09-28
### BREAKING CHANGES
- due to [`8b984f3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8b984f3dc947c4e42ff82efb7cac588828e5cc3f) - 统一使用 tracks 保存模板对象参数 *(commit by [@left0ver](https://github.com/left0ver))*:

  统一使用 tracks 保存模板对象参数

- due to [`36145fa`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/36145fa45df0a9f6eb42aaa27feea19b98cc0ab9) - return HTTP 200 creation envelope *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*:

  POST /api/v1/video-compositions now returns HTTP 200 instead of 202; request-body validation errors use code/message/data.


### New Features
- [`cd3ab93`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/cd3ab936d9004c2227c6fa3d72092952ec247d9c) - **remotion**: delete chat history and associated data *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`3ae6241`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3ae62412893086ceb7463960b76952c93e530759) - **home**: add home tab with cloud and local template selection *(commit by [@left0ver](https://github.com/left0ver))*
- [`7edab4a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/7edab4a2058c7f10d122276894b2805c37bd7bda) - **client_template**: add asset-based template editing *(commit by [@left0ver](https://github.com/left0ver))*
- [`2d20636`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2d2063662c8b37f0d2838a53ec25591b2eb72912) - **templates**: 支持独立时间规则与独立的特效轨道 *(commit by [@left0ver](https://github.com/left0ver))*
- [`ece555a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ece555a7b0c5897370d64425662b77820011fbc6) - **client**: redesign template home and editor navigation *(commit by [@left0ver](https://github.com/left0ver))*
- [`feaac83`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/feaac8331351abbbaadc81c5d376de3c9dba36ad) - **client**: display changelog beside and compact home template panel *(commit by [@left0ver](https://github.com/left0ver))*
- [`e2c81f1`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e2c81f1faf29e8e16635c006cd9e6a95580db3ef) - **server**: improve video composition callbacks and recovery *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`9c6af80`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/9c6af801ee74d9e6fec87787dc3ee21e9f2f682a) - **server**: add MySQL CA support and align renderer defaults *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`e35e6f6`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e35e6f6e8876fc41cc3262742937a69746d5596b) - **templates**: preview at native resolution and support larger text *(commit by [@left0ver](https://github.com/left0ver))*
- [`1a2c307`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1a2c307d48c5ba721ef3975466c7bdf52231cd3e) - **server**: expose segmentation trace on failures *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`b10ff65`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b10ff659bbf2447b454cde03acf56e17ac895f5d) - **segmentation**: add timed subtitle parts at punctuation *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`105efd5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/105efd596d3bd9d187cbe5da5e0d25257f0ba148) - **video-composition**: render timed subtitle clips *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`7ed4423`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/7ed44237afdf2655e78fdefac60d0d1cebef0942) - **video-composition**: add ZOS video transfer *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`5ecb6a1`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5ecb6a1b3332a4012ba889982ba6b8ec49f0e664) - **video-composition**: return persistent ZOS video URLs *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`9a47087`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/9a47087b5c077513da24f005757be16c65b68b3b) - **template**: use protobuf for template APIs *(commit by [@left0ver](https://github.com/left0ver))*
- [`6163ea2`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6163ea2446112b635b00dd0b68d0786e9c8d5c37) - **server**: upload third-frame composition covers to ZOS *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`48ea257`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/48ea257a8b12d104252c14ad8a1bd4fc2dc10d82) - **template**: use protobuf for template APIs *(PR [#63](https://github.com/HsiangNianian/IntelligentMixVideo/pull/63) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`c15366c`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c15366cd96995c73bf63fd5e20311e9b6e017e1f) - **template**: 支持底部字幕关键词局部样式与颜色 *(commit by [@left0ver](https://github.com/left0ver))*
- [`93aeb06`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/93aeb06f36d9f6245693f074c3fa54e0efdef2ab) - **template**: 支持顶部标题设置关键字 *(commit by [@left0ver](https://github.com/left0ver))*
- [`0696d63`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/0696d63513e951121718a9e50a69545478648b3b) - **templates**: support keyword font size *(commit by [@left0ver](https://github.com/left0ver))*
- [`01a91d1`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/01a91d1fd8d95ce34aba5d7077a29173f38ab56a) - **template**: add keyword styling for titles and subtitles *(PR [#70](https://github.com/HsiangNianian/IntelligentMixVideo/pull/70) by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`345dae4`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/345dae4e1421b797debd526d44b227161d8d2b65) - **client**: 优化模板编辑布局与时间轴交互 *(commit by [@left0ver](https://github.com/left0ver))*
- [`e12efa6`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e12efa63fc9c1762f791a50c8a8c4c77695f5f55) - **client**: redesign home page with gallery cards and changelog timeline *(commit by [@left0ver](https://github.com/left0ver))*
- [`34a4c22`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/34a4c226ce63865fdcb2b5457e6ff8f97dda4f04) - **proto**: define reusable Remotion Sprite contract *(PR [#71](https://github.com/HsiangNianian/IntelligentMixVideo/pull/71) by [@jyh20030112](https://github.com/jyh20030112))*
- [`9565e9f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/9565e9f43d7578336d63c3a5857ab16bbc791e50) - **client**: redesign home page and template editor layout *(PR [#73](https://github.com/HsiangNianian/IntelligentMixVideo/pull/73) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Bug Fixes
- [`dd642b0`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/dd642b031979182dc616205c19238b4f73277b1e) - **templates**: explain how to clear outdated local templates *(commit by [@left0ver](https://github.com/left0ver))*
- [`ddf07a5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ddf07a5e8bb7a6b992e261c22763d819d8f869ce) - **server**: adapt composition timeline to template tracks *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`4b2527a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4b2527a4f5ab7a77f4858d3eadf9ef687b64b3a8) - **client**: unwrap data wrapper in composition creation response *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`77a6a65`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/77a6a65770e1d1fa8686cd2116b589d8f6e3b4b9) - **server**: mark client IMS tasks unrecoverable after restart *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`87465ee`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/87465eedd063d88582c0ccec5031f5ef021dffbe) - **video-composition**: return task ID directly in creation response *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`b5aa0af`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b5aa0af415d152a96e3b167c81879b6c2518b372) - **video-composition**: preserve material aspect ratio with fit mode *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`5d8686c`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5d8686cf7b59119b5a702bd9fc35a455cd3ed67b) - **video-composition**: sync task response contract *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`751daf9`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/751daf9daf550021da6d1c5a547483a1d542b28c) - **client**: allow HTTP backend requests on macOS *(commit by [@left0ver](https://github.com/left0ver))*
- [`2d5153f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2d5153f2390ec0f423777b406f7a255dab540d91) - **server**: log segmentation diagnostics without response echo *(commit by [@muyuzhong](https://github.com/muyuzhong))*
- [`1cc0cb5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1cc0cb507af608953985179d7baec8ca94f57097) - **video-composition**: preserve media aspect ratio in timelines *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`36145fa`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/36145fa45df0a9f6eb42aaa27feea19b98cc0ab9) - **video-composition**: return HTTP 200 creation envelope *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`b4a18d1`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b4a18d17aae10fe6cd19949cd7d56ed5cad8b558) - **ci**: align desktop startup smoke with home page *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`68087b8`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/68087b89b3355c67c6cf157d506e27f4947088ad) - **client**: avoid duplicate macOS plist in tests *(commit by [@left0ver](https://github.com/left0ver))*
- [`7f8d261`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/7f8d261fc8aaa478d31f0be3f1e145e0d8066cc6) - **server**: preserve ZOS timeout stage across retries *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`eb844dc`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/eb844dc92721e42174e225bec1c6517a9558ab45) - **template**: apply server defaults to omitted local Protobuf editor fields *(commit by [@left0ver](https://github.com/left0ver))*
- [`84b4ea3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/84b4ea3b6e1df3b71ea026d522d5b90d41569cea) - **server**: document template Protobuf media types in OpenAPI *(commit by [@left0ver](https://github.com/left0ver))*
- [`a69ab43`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a69ab4303dd1dce384fab08bc131fc16a970d7e5) - **client**: restore protobuf template CI checks *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`e0bf1b8`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/e0bf1b83b252a0ee759dac1b457019d227aef2ca) - **template**: select two characters for title keywords *(commit by [@left0ver](https://github.com/left0ver))*
- [`9e62423`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/9e624231a6ed888ae7342b84d9857beee1f99aaa) - **template**: preserve title and subtitle preview text *(commit by [@left0ver](https://github.com/left0ver))*
- [`681a02d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/681a02d501151e9452c90a111242b967dffcd6d2) - **template**: clear cached preview timeline before updates *(commit by [@left0ver](https://github.com/left0ver))*
- [`1cf2649`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1cf2649592ae75c277a9858fcc308397fecb99ea) - **video-composition**: use template text for bubble tracks *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`2128e58`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2128e58794a6f0bf2817804f5c89c3def5607748) - **video-composition**: use template text for bubble tracks *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`b51e460`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b51e460b59aff23e0629d8dac927ac08b9fbc42e) - **client**: limit transition inspector to timing settings *(commit by [@left0ver](https://github.com/left0ver))*
- [`4f4eeb9`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/4f4eeb9fc577e8edda1b8d5872402c573c53ec02) - **client**: preserve template state across layout breakpoints *(commit by [@left0ver](https://github.com/left0ver))*
- [`a7ef86b`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a7ef86b13e2dd5917e576fbd46655d9153c2ca07) - **client**: keep preview seeking responsive during dragging *(commit by [@left0ver](https://github.com/left0ver))*

### Refactors
- [`d1713a0`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/d1713a021e60ccf032f61c69c4ec378d9eaa176c) - **server**: use explicit statements for chat deletion *(commit by [@jyh20030112](https://github.com/jyh20030112))*
- [`8b984f3`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/8b984f3dc947c4e42ff82efb7cac588828e5cc3f) - **templates**: 统一使用 tracks 保存模板对象参数 *(commit by [@left0ver](https://github.com/left0ver))*
- [`283c959`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/283c959cbc0ec41864f62898eb3315fb8216888d) - **client**: remove changelog and clock from home page *(commit by [@left0ver](https://github.com/left0ver))*

### Tests
- [`037a409`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/037a4090260b578c5a0a6fa34f057139259fcb05) - **client**: remove duplicate template storage test target *(commit by [@left0ver](https://github.com/left0ver))*
- [`25b1400`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/25b14003d84e52a01e3d9d8eaaa16b847cfd9672) - **templates**: focus coverage on core behavior *(commit by [@left0ver](https://github.com/left0ver))*
- [`d8bcf11`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/d8bcf11d7297c3cbc6c21804d8c6331fb758f711) - **server**: stabilize ZOS timeout assertion *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`ae13b25`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/ae13b251e082774305637063bd1166d27a24a364) - **server**: isolate FFmpeg availability in composition tests *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*

### Build System
- [`56e8c84`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/56e8c844386f0b726abf1111710a496afc4e44e3) - **server**: install FFmpeg for composition tests *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`cc600fb`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/cc600fb596ace18daf752eca6a1b0a0b4beaae64) - **server**: avoid installing FFmpeg in Python job *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*

### Documentation Changes
- [`725312e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/725312e1d7bd3dc72e27a128d6335772b579655e) - update CHANGELOG.md for v0.4.1 [skip ci] *(commit by [@github-actions[bot]](https://github.com/apps/github-actions))*
- [`6bfca32`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6bfca32a66425aabf7e9b7de4d95d29d8fc117ce) - **server**: document video composition behavior *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`552d90d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/552d90db97b5c1e34597e13ac43319f6148f0105) - document punctuation-aware subtitle timing *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`3d37ba2`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/3d37ba28484e4af50073f4b8c750cc9c1bf3a982) - **video-composition**: document ZOS output links *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`7a5f802`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/7a5f80282b4df4a2b300e17e350edde7a7e885c6) - **proto**: 添加protobuf字段的注释 *(commit by [@left0ver](https://github.com/left0ver))*
- [`6c295d5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/6c295d5a1cd52d29cc2f40fe7ed0a924cf202348) - **server**: document ZOS covers and custom public URLs *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`db797a4`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/db797a4426eaba42e9f6bfb6be241c523cca6901) - **video-composition**: clarify bubble and keyword rendering *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`27c6981`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/27c6981782032481265a19b1493bfbaefcbf9891) - **video-composition**: clarify bubble and keyword rendering *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*

### Code Style Changes
- [`49d34f8`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/49d34f8b928803b304e0916f2ffd68f95ce6cd69) - **server**: standardize module docstrings and import ordering *(commit by [@left0ver](https://github.com/left0ver))*

### Chores
- [`1b9445d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/1b9445d7636303efbb8c1dacd9622b99bb392961) - add  .DS_Store to .gitignore *(commit by [@left0ver](https://github.com/left0ver))*
- [`a2b21f9`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a2b21f90a7207aa6662d16d4297eccc80421d7b0) - merge upstream dev with subtitle diagnostics *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`9655c6e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/9655c6eaaa62652f5957d483416c93d01c434d6a) - merge upstream dev into ZOS branch *(commit by [@IT-coder-Yy](https://github.com/IT-coder-Yy))*
- [`5e0ff8d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5e0ff8d0613f3fb5053964ab689f794a3205b796) - 修改默认的预览视频 *(commit by [@left0ver](https://github.com/left0ver))*
- [`d403e03`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/d403e039c5d73629af132bf1501da5e7e17b4dc4) - 修改关键词样式文案 *(commit by [@left0ver](https://github.com/left0ver))*
- [`5014930`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5014930f808947d8fd8613dc4199632016f4cb2d) - update gitignore *(commit by [@left0ver](https://github.com/left0ver))*
- [`a8f8f5e`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a8f8f5ed60fea24b7ba7340d4400dc13e6d82eb5) - **release**: prepare v0.4.2 *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`195f96d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/195f96da7361a050ef43a72e1100b99af3d661da) - **release**: promote dev to main for v0.4.2 *(PR [#75](https://github.com/HsiangNianian/IntelligentMixVideo/pull/75) by [@YUZHEthefool](https://github.com/YUZHEthefool))*

### Other Changes
- [`253109a`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/253109a7666590b13e5ecca24e866960708b260b) - Merge pull request [#52](https://github.com/HsiangNianian/IntelligentMixVideo/pull/52) from jyh20030112/feat/delete-remotion-chats

feat(remotion): delete chat history and associated data *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`b3d3639`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/b3d3639b6036e148cc320f1a2f0f541218ce1155) - Merge pull request [#54](https://github.com/HsiangNianian/IntelligentMixVideo/pull/54) from left0ver/feature/add_home_track

feat(templates)!: add template home and independent effect tracks *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`7dc29a1`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/7dc29a1b2047cd6f33ef030bfe5b6bc15af5ee4c) - Merge pull request [#56](https://github.com/HsiangNianian/IntelligentMixVideo/pull/56) from IT-coder-Yy/dev

feat: Improve video composition callbacks and template track compatibility *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`17a0d94`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/17a0d943f144a993d8a7982fd80c61a52d12b295) - Update SSL connection settings for PyMySQL

Co-authored-by: Copilot Autofix powered by AI <175728472+Copilot@users.noreply.github.com> *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`64f606d`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/64f606d6a53928c598e674f24671876a0e6dbfea) - Merge pull request [#57](https://github.com/HsiangNianian/IntelligentMixVideo/pull/57) from HsiangNianian/feat/server-mysql-ca-and-host-defaults

feat(server): add MySQL CA support and align renderer defaults *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`2b2b673`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/2b2b6733400c95b2f93efdd36e97be2590956977) - Merge pull request [#58](https://github.com/HsiangNianian/IntelligentMixVideo/pull/58) from IT-coder-Yy/dev

fix(video-composition): preserve material aspect ratio *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`67e59b5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/67e59b5be55753c86255f9b1deed42ad1df5739c) - Merge pull request [#60](https://github.com/HsiangNianian/IntelligentMixVideo/pull/60) from left0ver/codex/upstream-dev

feat(client): improve template previews and macOS HTTP access *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`43c6987`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/43c69870ea2ea4aaf74ca0a46712f796145b830e) - Merge pull request [#61](https://github.com/HsiangNianian/IntelligentMixVideo/pull/61) from muyuzhong/feat/segmentation-trace-observability *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`a4dc78f`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a4dc78f2381832db0d9fa086c5d313e21dd41f0d) - Merge pull request [#62](https://github.com/HsiangNianian/IntelligentMixVideo/pull/62) from IT-coder-Yy/dev

feat(video-composition)!: split subtitles and standardize task creation *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`c0c25f6`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c0c25f68b07ad715ea95342b688bfe1c3843e83f) - Merge pull request [#64](https://github.com/HsiangNianian/IntelligentMixVideo/pull/64) from IT-coder-Yy/feat/video_ZOS

feat(video-composition): persist finished videos in ZOS *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`cc5d112`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/cc5d1126d387f6bc5670a93e74ae786ebec1e7dd) - Merge pull request [#65](https://github.com/HsiangNianian/IntelligentMixVideo/pull/65) from HsiangNianian/dev

feat: sync dev code to main *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`00b72e6`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/00b72e602af007a303bf0f9822066779996339a5) - Merge pull request [#67](https://github.com/HsiangNianian/IntelligentMixVideo/pull/67) from IT-coder-Yy/feat/video_ZOS

feat(server): publish third-frame video composition covers to ZOS *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`83be2d7`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/83be2d7ad4d3e2edbc01f2344cbeb1b41b05ebe9) - Update server/src/server/template/router.py

Co-authored-by: sourcery-ai[bot] <58596630+sourcery-ai[bot]@users.noreply.github.com> *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`4137656`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/413765607c0cae2895739c9caec7e9f9daa331a4) - Handle optional fields in JSON response

Co-authored-by: Copilot Autofix powered by AI <175728472+Copilot@users.noreply.github.com> *(commit by [@HsiangNianian](https://github.com/HsiangNianian))*
- [`c3ac5d0`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/c3ac5d05e9e1b050f2e43dc6a56ac1a08c1c5016) - Update client/src-tauri/src/templates.rs

Co-authored-by: Thefool <thefoolyuzhe@gmail.com> *(commit by [@left0ver](https://github.com/left0ver))*
- [`5ccaf76`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/5ccaf769af9103d20a49eccb97542900595e57e5) - Merge pull request [#72](https://github.com/HsiangNianian/IntelligentMixVideo/pull/72) from IT-coder-Yy/feat/video_composition

fix(video-composition): render bubbles from template text *(commit by [@YUZHEthefool](https://github.com/YUZHEthefool))*
- [`a0c17a5`](https://github.com/HsiangNianian/IntelligentMixVideo/commit/a0c17a5e4ee982d33c0865ba142c55a3e3237524) - Merge branch 'dev' into refactor/optimize_frontend_ui *(commit by [@left0ver](https://github.com/left0ver))*

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

[v0.4.2]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.4.1...v0.4.2

[v0.5.0]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.4.2...v0.5.0

[v0.9.0]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.5.0...v0.9.0

[v0.9.1]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.0...v0.9.1

[v0.9.2]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.1...v0.9.2

[v0.9.3]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.2...v0.9.3

[v0.9.4]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.3...v0.9.4

[v0.9.5]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.4...v0.9.5

[v0.9.6]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.5...v0.9.6

[v0.9.7]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.6...v0.9.7

[v0.9.8]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.7...v0.9.8

[v0.9.9]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.8...v0.9.9

[v0.9.10]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.9...v0.9.10

[v0.9.11]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.10...v0.9.11

[v0.9.12]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.11...v0.9.12

[v0.9.13]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.12...v0.9.13

[v0.9.14]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.13...v0.9.14

[v0.9.15]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.14...v0.9.15

[v0.9.16]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.15...v0.9.16

[v0.9.17]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.16...v0.9.17

[v0.9.18]: https://github.com/HsiangNianian/IntelligentMixVideo/compare/v0.9.17...v0.9.18
