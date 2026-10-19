/**
 * 设计沙盒的假数据与原型状态类型。模板摘要沿用 features/templates/model.ts 的 TemplateSummary 字段名，
 * 接入真实接口时只需替换数据来源；时间按页面打开时刻倒推，让「今天 / 昨天」等相对日期始终有代表性。
 */

/** 两个模板库：云端（团队共享，保存在服务端）与本地（仅保存在本机，需要桌面客户端）。 */
export type Library = "cloud" | "local";

/** 与正式 TemplateSummary 同名字段；validation_error 非空表示配置无效、不能打开。 */
export interface TemplateSummary {
  template_id: string;
  name: string;
  description: string;
  updated_at: string;
  title: string;
  subtitle: string;
  track_count: number;
  validation_error?: string;
}

/** 原型面板可以切换的界面状态，用来逐一检查加载、失败、空列表与浏览器环境的样子。 */
export interface ProtoState {
  /** 配色：light 是选定的冷白；graphite 是深色主题候选，对应 design.css 里的 data-palette。 */
  palette: "light" | "graphite";
  runtime: "desktop" | "browser";
  cloud: "ready" | "loading" | "error" | "empty";
  local: "ready" | "loading" | "empty";
  boot: "off" | "starting" | "failed";
  dirty: boolean;
  deleteFails: boolean;
}

export const defaultProto: ProtoState = {
  palette: "light",
  runtime: "desktop",
  cloud: "ready",
  local: "ready",
  boot: "off",
  dirty: true,
  deleteFails: false,
};

/** 返回距现在若干小时的 ISO 时间。 */
function hoursAgo(hours: number): string {
  return new Date(Date.now() - hours * 3_600_000).toISOString();
}

export const cloudTemplates: TemplateSummary[] = [
  { template_id: "3f6c1a2e-8b7d-4e15-9a0c-5d2b7e914c61", name: "带货口播 · 黄色花字", description: "醒目黄字搭配清晰字幕，适合产品卖点与好物分享。", updated_at: hoursAgo(1.5), title: "好物推荐 现在就出发", subtitle: "选择花字、滤镜和特效，看看组合效果", track_count: 4 },
  { template_id: "a91d04b7-2c3e-4f58-8d61-0b9e7c2a4f13", name: "旅行 Vlog · 金色标题", description: "记录沿途风景，用暖金标题讲述旅行故事。", updated_at: hoursAgo(26), title: "下一站 去看世界", subtitle: "跟着镜头一起出发", track_count: 2 },
  { template_id: "5be2f8c0-71a4-4d93-b6e5-28c4d0f7a9e2", name: "知识分享 · 简洁字幕", description: "清晰呈现知识重点，适合教程讲解与日常科普。", updated_at: hoursAgo(50), title: "每天学会一个新知识", subtitle: "三分钟讲清楚一个概念", track_count: 3 },
  { template_id: "c07a3e91-d4b2-4a6f-9e18-7f35b2c6d804", name: "美食探店 · 暖色手写", description: "温暖标题配合轻快转场，适合探店与菜谱。", updated_at: hoursAgo(120), title: "这家店 排队也值得", subtitle: "人均 45 · 招牌必点", track_count: 6 },
  { template_id: "e4d61b2f-93c8-4e07-a5b1-6c2f8d9e0a37", name: "新品发布 · 电影感", description: "宽银幕质感标题与慢速推镜转场。", updated_at: hoursAgo(300), title: "全新登场", subtitle: "十月十九日 · 敬请期待", track_count: 5 },
  { template_id: "08b3c9d4-5e6f-4a71-b2c8-d9e0f1a2b3c4", name: "旧版双语字幕", description: "", updated_at: hoursAgo(2200), title: "", subtitle: "", track_count: 0, validation_error: "模板缺少 tracks 字段，无法打开。可以删除后重新创建。" },
];

export const localTemplates: TemplateSummary[] = [
  { template_id: "71e2d3c4-b5a6-4978-8c9d-0e1f2a3b4c5d", name: "门店活动 · 红色促销", description: "限时活动信息突出显示，适合门店短视频。", updated_at: hoursAgo(4), title: "限时三天 全场八折", subtitle: "到店扫码再领一张券", track_count: 3 },
  { template_id: "9c8b7a6d-5e4f-4321-a0b9-c8d7e6f5a4b3", name: "日常记录 · 极简", description: "只保留必要的标题与字幕。", updated_at: hoursAgo(75), title: "周末的小确幸", subtitle: "", track_count: 2 },
];
