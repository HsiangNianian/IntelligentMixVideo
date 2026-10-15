/** 应用名称、版本与构建校验码：版本在构建时从 package.json 注入（发版脚本同步它与 Tauri、服务端的版本），界面不另写一处。 */

/** 当前客户端版本；没有注入（例如工具链之外直接运行源码）时显示 dev。 */
export const APP_VERSION: string = import.meta.env.IMV_VERSION || "dev";

/** 构建对应的校验码：短提交哈希，工作区有未提交改动时带 -dirty；没有注入时显示 unknown。 */
export const APP_COMMIT: string = import.meta.env.IMV_COMMIT || "unknown";

/** 窗口与网页标题：应用名后接版本号。 */
export const WINDOW_TITLE = `IntelligentMixVideo - v${APP_VERSION}`;
