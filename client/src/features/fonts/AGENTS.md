# 字体组件协作约定

适用于 `client/src/features/fonts/`，补充根目录与 `client/` 的 `AGENTS.md`。

服务端字体库（`remotion_fonts`、上传接口、渲染沙箱字体）见 `server/src/server/remotion_templates/AGENTS.md`；IMS 模板字体目录见 `server/src/server/template/AGENTS.md`。字体库页面的完整行为见 `client/README.md`「字体库页面」。

- IMS 与 Remotion 的字体选择共用 `FontSelect.tsx`（搜索、字形预览、选中状态与附加操作）与 `FontName.tsx`（透明预览条按主题文字色显示）。预览条按文字实际宽度绘制，不超过 640×64。
- `TemplateFontPicker` 只用模板的十一种字体与随包 PNG（生成方式见根目录 `AGENTS.md`「生成文件」），浏览选项不需要联网。Remotion 的 `FontPicker` 读实时字体库并提供上传，`ParameterControl` 对名为 `fontFamily` 的参数一律用它，聊天参数面板与模板编辑的「外观与位置」共用。
- 字体显示名的读取规则写在 `names.ts`，与服务端读 name 表的规则一致；上传的 `label` 就是按它读出、画在预览条上的显示名。
- 去重靠文件摘要：上传前按 SHA-256 在已读到的字体库里查找，已有同一个文件就直接选用；修复前的原文件已登记时也不上传修复版。请求失败后重新读取字体库，按摘要找到就视为已登记，不重复上传（超时或断线时服务端可能已经登记）。服务端回答 200 时提示「字体已在字体库里」。不在安全上下文的页面没有摘要能力，只能依赖服务端的幂等回答。
- 修复只在浏览器端的 `repair.ts` 做，后端不参与：只在目录里确实有长度为 0 的可选表时重写，修复幂等，不改字形、name、cmap 等表的数据；摘要、预览条与上传都用修复后的字节。文件结构无法修复或修复后浏览器仍解不开时，说明原因且不发上传请求。
- 字体库页面（`FontLibrary`）只在当前页签挂载，每次打开重新读取；它与下拉选择器共用 `useFonts`。字样预览注册的临时 `FontFace` 在离开时移除。
