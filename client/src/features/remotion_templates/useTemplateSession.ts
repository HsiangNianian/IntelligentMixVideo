/** 协调单会话请求、轮询和参数验收；会话切换隔离迟到响应，卸载清理计时器与任务。 */
import { useEffect, useRef, useState } from "react";
import * as api from "./api";
import {
  sameValues,
  type ChatMessage,
  type Job,
  type Scalar,
  type Values,
  type Version,
} from "./model";

/** 会话保留成功结果与一份待验收参数，任务终态决定何时替换。 */
interface Session {
  key: number;
  messages: ChatMessage[];
  workId: string | null;
  job: Job | null;
  version: Version | null;
  values: Values;
  code: string;
  busy: "chat" | "parameters" | null;
  error: string;
  retryMode: "poll" | "job" | null;
}
/** 每个空白会话拥有独立草稿；首次发送前不创建服务端作品。 */
function blank(key: number): Session {
  return {
    key,
    messages: [],
    workId: null,
    job: null,
    version: null,
    values: {},
    code: "",
    busy: null,
    error: "",
    retryMode: null,
  };
}
/** 单个轮询间隔可被取消，避免卸载后的遗留定时器。 */
function delay(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, 1000);
    // 解除监听并拒绝等待，让轮询及时结束。
    function abort() {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      reject(new DOMException("Aborted", "AbortError"));
    }
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}

/** 保留成功版本与临时参数两个快照；自然语言修改只在参数验收完成后发送。 */
export function useTemplateSession() {
  const [state, setState] = useState<Session>(() => blank(0));
  const latest = useRef(state);
  const alive = useRef(true);
  const scope = useRef(new AbortController());
  const timer = useRef<number | undefined>(undefined);
  const lastKind = useRef<"chat" | "parameters">("chat");
  const flush = useRef<() => void>(() => {});

  /** 同步更新事件读取的快照，避免连续事件使用旧 React state。 */
  function publish(patch: Partial<Session>) {
    latest.current = { ...latest.current, ...patch };
    if (alive.current) setState(latest.current);
  }
  /** 会话切换或卸载后拒绝迟到响应。 */
  function current(key: number) {
    return alive.current && latest.current.key === key;
  }
  /** 仅追加公开聊天消息，不展示内部候选或修复轨迹。 */
  function append(role: ChatMessage["role"], text: string, image?: File) {
    publish({
      messages: [
        ...latest.current.messages,
        { id: crypto.randomUUID(), role, text, image },
      ],
    });
  }
  /** 尚未验收的参数使发送和复制保持锁定。 */
  function dirty() {
    const s = latest.current;
    return (
      !!s.version && !sameValues(s.values, s.version.candidate.default_config)
    );
  }
  /** 短延迟提交当前参数，在切换会话时清理计时器。 */
  function schedule() {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => flush.current(), 650);
  }

  /** 一次执行仅消费所属会话的结果；新会话不接收旧模板或旧错误。 */
  async function observe(initial: Job, key: number) {
    let job = initial;
    while (current(key)) {
      publish({ job });
      if (job.status === "queued" || job.status === "running") {
        await delay(scope.current.signal);
        job = await api.job(job.id, scope.current.signal);
        continue;
      }
      if (job.status === "succeeded" && job.result_version_id) {
        const [version, code] = await Promise.all([
          api.version(job.result_version_id, scope.current.signal),
          api.exported(job.result_version_id, scope.current.signal),
        ]);
        if (!current(key)) return;
        publish({
          version,
          code,
          values: { ...version.candidate.default_config },
          error: "",
          retryMode: null,
        });
        if (latest.current.busy === "chat")
          append(
            "assistant",
            "模板已就绪。可以在右侧调整参数，或继续描述你想修改的效果。",
          );
      } else if (job.status === "needs_input") {
        append("assistant", job.questions.join("\n"));
      } else {
        publish({
          values: { ...latest.current.version?.candidate.default_config },
          error:
            job.status === "cancelled"
              ? "已停止本次制作。"
              : job.message || "本次未能完成，已有模板仍可使用。",
          retryMode: "job",
        });
      }
      return;
    }
  }

  /** 写入只发送一次；即使中途新增会话，收到旧任务 ID 后仍会取消该任务。 */
  async function execute(
    kind: "chat" | "parameters",
    start: () => Promise<Job>,
    resume = false,
  ) {
    if (latest.current.busy) return;
    const key = latest.current.key;
    lastKind.current = kind;
    let acknowledged = resume;
    publish({ busy: kind, error: "", retryMode: null });
    try {
      const job = await start();
      if (!current(key)) {
        await api.cancel(job.id);
        return;
      }
      acknowledged = true;
      publish({ workId: job.project_id, job });
      await observe(job, key);
    } catch (error) {
      if (current(key))
        publish({
          error:
            error instanceof Error ? error.message : "请求未完成，请重试。",
          retryMode: acknowledged ? "poll" : null,
          ...(!acknowledged && kind === "parameters"
            ? {
                values: { ...latest.current.version?.candidate.default_config },
              }
            : {}),
        });
    } finally {
      if (current(key)) {
        publish({ busy: null });
        if (
          dirty() &&
          !latest.current.error &&
          latest.current.job?.status !== "needs_input"
        )
          schedule();
      }
    }
  }

  /** 首轮可带参考图片；后续聊天只能修改或回答当前任务的问题。 */
  function send(text: string, image?: File) {
    const s = latest.current;
    if (s.busy || dirty() || (!text.trim() && !image)) return;
    append("user", text.trim(), image);
    void execute("chat", async () => {
      if (!s.workId) {
        const asset = image ? await api.upload(image) : undefined;
        if (!current(s.key)) throw new DOMException("Aborted", "AbortError");
        return (await api.create(text, asset?.id)).job;
      }
      return api.message(s.workId, {
        instruction: text.trim(),
        ...(s.job?.status === "needs_input"
          ? { reply_to_job_id: s.job.id }
          : { base_version_id: s.version?.id }),
      });
    });
  }

  /** 参数先更新预览并立即锁定，随后提交验收；运行中不接收第二份草稿。 */
  function change(key: string, value: Scalar) {
    const s = latest.current;
    if (
      !s.version ||
      s.busy ||
      dirty() ||
      s.retryMode === "poll" ||
      ["queued", "running", "needs_input"].includes(s.job?.status ?? "")
    )
      return;
    publish({
      values: { ...s.values, [key]: value },
      error: "",
      retryMode: null,
    });
    schedule();
  }
  flush.current = () => {
    const s = latest.current;
    if (
      !s.version ||
      !s.workId ||
      s.busy ||
      !dirty() ||
      s.error ||
      s.job?.status === "needs_input"
    )
      return;
    const values = { ...s.values };
    void execute("parameters", () =>
      api.message(s.workId!, {
        parameters: values,
        base_version_id: s.version!.id,
      }),
    );
  };

  /** 主动停止服务端任务；轮询读取终态后统一恢复最后成功参数。 */
  async function stop() {
    const s = latest.current;
    if (!s.job || !["queued", "running"].includes(s.job.status)) return;
    try {
      await api.cancel(s.job.id);
    } catch {
      if (current(s.key)) publish({ error: "停止请求未完成，请重试。" });
    }
  }

  /** 明确恢复读取或重新执行，网络失败不会自动重复提交。 */
  function retry() {
    const s = latest.current;
    if (!s.job || s.busy) return;
    void execute(
      lastKind.current,
      () =>
        s.retryMode === "poll"
          ? api.job(s.job!.id, scope.current.signal)
          : api.retry(s.job!.id),
      s.retryMode === "poll",
    );
  }

  /** 新增不删除历史作品；取消已知旧任务，隔离仍在传输中的响应。 */
  function reset() {
    const old = latest.current;
    scope.current.abort();
    scope.current = new AbortController();
    window.clearTimeout(timer.current);
    latest.current = blank(old.key + 1);
    setState(latest.current);
    if (old.job && ["queued", "running"].includes(old.job.status)) {
      void api.cancel(old.job.id).catch(() => {
        if (current(old.key + 1))
          publish({
            error: "新会话已打开，但旧任务停止失败。请检查服务端任务状态。",
          });
      });
    }
  }

  useEffect(() => {
    alive.current = true;
    // StrictMode 会先清理再挂载 effect；新的一轮读取必须使用未中断的 controller。
    if (scope.current.signal.aborted) scope.current = new AbortController();
    return () => {
      alive.current = false;
      scope.current.abort();
      window.clearTimeout(timer.current);
      const job = latest.current.job;
      if (job && ["queued", "running"].includes(job.status))
        void api.cancel(job.id).catch(() => {});
    };
  }, []);
  return {
    ...state,
    dirty:
      !!state.version &&
      !sameValues(state.values, state.version.candidate.default_config),
    send,
    change,
    stop,
    retry,
    reset,
  };
}
