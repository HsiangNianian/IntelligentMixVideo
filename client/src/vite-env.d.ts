// 声明 Vite 提供的资源导入与客户端环境变量类型；仅参与类型检查。
/// <reference types="vite/client" />

/** 构建时注入的 API 与示例视频地址；未填写时使用各自默认值。 */
interface ImportMetaEnv {
  readonly IMV_DEBUG?: string;
  readonly VITE_API_URL?: string;
  readonly VITE_PREVIEW_VIDEO_URL?: string;
}
