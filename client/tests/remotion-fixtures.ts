/** Remotion 测试数据：最小成功模板及公开任务，不包含真实服务地址或密钥。 */
import type { Job, Values, Version } from "@/features/remotion_templates/model";

/** 每次创建独立参数，便于模拟连续调整而不共享测试状态。 */
export function remotionVersion(id = "version-1", patch: Values = {}): Version {
  return {
    id,
    project_id: "work-1",
    number: Number(id.match(/\d+$/)?.[0] ?? 1),
    source: "agent",
    created_at: "2026-09-14T08:01:08Z",
    candidate: {
      tsx_code: "source code",
      default_config: {
        text: "今日灵感",
        size: 64,
        x: 0.5,
        color: "#FFFFFF",
        ...patch,
      },
      config_schema: {
        properties: {
          text: { type: "string", "x-imv-target": "/text_layers/0/text" },
          size: {
            type: "number",
            "x-imv-target": "/text_layers/0/style/font_size",
          },
          x: { type: "number", "x-imv-target": "/text_layers/0/layout/x" },
          color: {
            type: "string",
            "x-imv-target": "/text_layers/0/style/color",
          },
        },
      },
    },
    spec: {
      name: "今日灵感",
      composition: {
        width: 1080,
        height: 1920,
        fps: 30,
        duration_in_frames: 150,
      },
      text_layers: [{ id: "title", text: "今日灵感" }],
    },
  };
}
/** 公开任务只有状态和结果，不提供内部候选及修复日志。 */
export function remotionJob(
  status: Job["status"] = "succeeded",
  result = "version-1",
): Job {
  return {
    id: "job-1",
    project_id: "work-1",
    status,
    result_version_id: status === "succeeded" ? result : null,
    questions: status === "needs_input" ? ["标题写什么？"] : [],
    message:
      status === "failed"
        ? "本次未能完成模板，请重试；已有结果仍可使用。"
        : null,
  };
}
