/** 会话协调器：恢复公开快照、消费 SSE、提交用户操作；只接受成功版本，切换不取消后台任务。 */
import { useEffect, useRef, useState } from "react";
import * as api from "./api";
import { events, StreamReset } from "./events";
import {
  defaultComposition,
  resolveComposition,
  type CompositionDraft,
} from "./composition";
import {
  sameValues,
  validValue,
  type ChatMessage,
  type Job,
  type Scalar,
  type SessionJob,
  type Values,
  type Version,
} from "./model";

/** 当前视图状态与服务端历史分离，临时参数只在验收成功后成为可复制默认值。 */
interface Session {
  key: number;
  compositionDraft: CompositionDraft;
  workId: string | null;
  messages: ChatMessage[];
  job: Job | SessionJob | null;
  jobs: Record<string, SessionJob>;
  version: Version | null;
  previewVersion: Version | null;
  previewLoading: boolean;
  previewError: string;
  versionFailure: { id: string; message: string } | null;
  values: Values;
  code: string;
  busy: "chat" | "parameters" | null;
  loading: boolean;
  olderLoading: boolean;
  nextBefore: number | null;
  connection: "connecting" | "live" | "reconnecting" | null;
  error: string;
  retryMode: "read" | "job" | null;
  navigation:
    | (({ work: string | null } | { version: string }) & { saving: boolean })
    | null;
}
/** 新增只建立本地空白页，首次发送才创建持久会话。 */
function blank(key: number): Session {
  return {
    key,
    compositionDraft: defaultComposition(),
    workId: null,
    messages: [],
    job: null,
    jobs: {},
    version: null,
    previewVersion: null,
    previewLoading: false,
    previewError: "",
    versionFailure: null,
    values: {},
    code: "",
    busy: null,
    loading: false,
    olderLoading: false,
    nextBefore: null,
    connection: null,
    error: "",
    retryMode: null,
    navigation: null,
  };
}
/** 本地仅记录当前服务最后选中的 ID；浏览器禁用存储时仍可使用历史列表。 */
const selectedKey = `imv.remotion.selected:${api.apiUrl("")}`;
/** 合并有稳定 ID 的服务端消息，按持久序号排序；临时消息不参与历史恢复。 */
function mergeMessages(previous: ChatMessage[], incoming: ChatMessage[]) {
  return [
    ...new Map(
      [
        ...previous.filter((message) => message.sequence !== undefined),
        ...incoming,
      ].map((message) => [message.id, message]),
    ).values(),
  ].sort((left, right) => (left.sequence ?? 0) - (right.sequence ?? 0));
}
/** 可中断的重连退避，组件退出时不遗留定时器或监听器。 */
function reconnectDelay(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(done, 2000);
    /** 到时和中断共用清理，避免旧会话继续尝试重连。 */
    function done() {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done, { once: true });
    if (signal.aborted) done();
  });
}

/** 一条当前会话订阅支持多个任务；只在明确点击停止时调用取消 API。 */
export function useTemplateSession(onHistoryChange: () => void) {
  const [state, setState] = useState<Session>(() => blank(0));
  const latest = useRef(state);
  const alive = useRef(true);
  const scope = useRef(new AbortController());
  const parameterSave = useRef(false);
  const previewRequest = useRef<AbortController | null>(null);
  const changed = useRef(onHistoryChange);
  changed.current = onHistoryChange;

  /** 同步推进快照，避免连续 SSE 事件读取上一轮 React state。 */
  function publish(patch: Partial<Session>) {
    latest.current = { ...latest.current, ...patch };
    if (alive.current) setState(latest.current);
  }
  /** 所有读取与写入回执均受会话代号约束，迟到结果不能污染另一会话。 */
  function current(key: number, signal?: AbortSignal) {
    return alive.current && latest.current.key === key && !signal?.aborted;
  }
  /** 仅缓存选择位置，不缓存作为事实来源的聊天内容或代码。 */
  function remember(id: string | null) {
    try {
      if (id) localStorage.setItem(selectedKey, id);
      else localStorage.removeItem(selectedKey);
    } catch {
      /* 存储不可用不影响服务端历史。 */
    }
  }
  /** 版本、导出和参数原子替换；产物失败独立记录，不阻塞公开事件游标。 */
  async function accept(id: string, key: number, signal: AbortSignal) {
    if (latest.current.version?.id === id) return;
    try {
      const [version, code] = await Promise.all([
        api.version(id, signal),
        api.exported(id, signal),
      ]);
      if (!current(key, signal)) return;
      if (version.project_id !== latest.current.workId)
        throw new Error("版本不属于当前会话。");
      publish({
        version,
        code,
        values: { ...version.candidate.default_config },
        versionFailure: null,
      });
      parameterSave.current = false;
    } catch (error) {
      if (!current(key, signal)) return;
      publish({
        versionFailure: {
          id,
          message: `新版本读取失败：${error instanceof Error ? error.message : "无法读取版本产物。"} 可重新读取结果。${latest.current.version ? "已有代码仍可使用。" : "暂未取得可用代码。"}`,
        },
        navigation: latest.current.navigation
          ? { ...latest.current.navigation, saving: false }
          : null,
      });
    }
  }
  /** 公开任务决定操作锁；成功版本读取完成或明确读取失败后解锁。 */
  function applyJob(job: Job | SessionJob) {
    const active = job.status === "queued" || job.status === "running";
    const waitingVersion =
      job.status === "succeeded" &&
      job.result_version_id !== latest.current.version?.id &&
      job.result_version_id !== latest.current.versionFailure?.id;
    const failed = ["failed", "interrupted", "cancelled"].includes(job.status);
    if (failed) parameterSave.current = false;
    publish({
      job,
      ...("created_at" in job
        ? { jobs: { ...latest.current.jobs, [job.id]: job } }
        : {}),
      busy:
        active || waitingVersion
          ? "parameters" in job && job.parameters
            ? "parameters"
            : "chat"
          : null,
      ...(active &&
      "parameters" in job &&
      job.parameters &&
      latest.current.version
        ? {
            values: {
              ...latest.current.version.candidate.default_config,
              ...job.parameters,
            },
          }
        : {}),
      ...(failed
        ? {
            error: job.message ?? "本次制作已停止，已有结果仍可使用。",
            retryMode: dirty() ? null : ("job" as const),
            navigation: latest.current.navigation
              ? { ...latest.current.navigation, saving: false }
              : null,
          }
        : { error: "", retryMode: null }),
    });
    const navigation = latest.current.navigation;
    if (
      navigation?.saving &&
      job.status === "succeeded" &&
      !waitingVersion &&
      !dirty()
    )
      completeNavigation(navigation);
  }
  /** 先恢复公开快照再推进游标，产物失败独立保留为可重试状态。 */
  async function hydrate(work: string, key: number, signal: AbortSignal) {
    const snapshot = await api.session(work, signal);
    if (!current(key, signal)) return snapshot.cursor;
    publish({
      messages: snapshot.messages,
      nextBefore: snapshot.next_before,
      jobs: Object.fromEntries(
        (snapshot.jobs ?? []).map((job) => [job.id, job]),
      ),
    });
    if (snapshot.work.current_version_id)
      await accept(snapshot.work.current_version_id, key, signal);
    if (current(key, signal)) applyJob(snapshot.job);
    return snapshot.cursor;
  }
  /** 断线从最后已应用的 ID 续传；游标失效才重新取快照，永不重发 POST。 */
  async function watch(
    work: string,
    key: number,
    signal: AbortSignal,
    cursor: number,
  ) {
    while (current(key, signal)) {
      try {
        for await (const event of events(work, cursor, signal, () => {
          if (current(key, signal)) publish({ connection: "live" });
        })) {
          if (!current(key, signal)) return;
          if (event.id <= cursor) continue;
          if (event.type === "message.created")
            publish({
              messages: mergeMessages(latest.current.messages, [event.data]),
            });
          else if (event.type === "job.updated") applyJob(event.data);
          else {
            await accept(event.data.version_id, key, signal);
            if (!current(key, signal)) return;
            if (latest.current.job) applyJob(latest.current.job);
          }
          cursor = event.id;
          if (event.type !== "message.created")
            if (alive.current) changed.current();
        }
      } catch (error) {
        if (!current(key, signal)) return;
        publish({ connection: "reconnecting" });
        if (error instanceof StreamReset) {
          try {
            cursor = await hydrate(work, key, signal);
          } catch {
            /* 下一次连接仍以原游标触发快照恢复。 */
          }
        }
        await reconnectDelay(signal);
      }
    }
  }
  /** 重新读取当前会话时替换旧订阅，保持同一聊天输入框和播放器实例。 */
  async function attach(work: string, key: number) {
    scope.current.abort();
    const controller = new AbortController();
    scope.current = controller;
    publish({
      workId: work,
      loading: true,
      connection: "connecting",
      error: "",
      retryMode: null,
    });
    remember(work);
    try {
      const cursor = await hydrate(work, key, controller.signal);
      if (!current(key, controller.signal)) return;
      publish({ loading: false });
      void watch(work, key, controller.signal, cursor);
    } catch (error) {
      if (current(key, controller.signal))
        publish({
          loading: false,
          busy: null,
          connection: null,
          error: error instanceof Error ? error.message : "会话恢复失败。",
          retryMode: "read",
        });
    }
  }
  /** 切换、新增和卸载只清理本地读取；任务继续写入其所属历史。 */
  function switchWork(work: string | null) {
    scope.current.abort();
    previewRequest.current?.abort();
    parameterSave.current = false;
    const next = blank(latest.current.key + 1);
    latest.current = next;
    setState(next);
    remember(work);
    if (work) void attach(work, next.key);
    if (alive.current) changed.current();
  }
  /** 未保存参数必须先决定去留，同一会话的重复选择不清空草稿。 */
  function select(work: string | null) {
    if (work !== null && work === latest.current.workId) return;
    if (dirty()) publish({ navigation: { work, saving: false } });
    else switchWork(work);
  }
  /** 历史预览只读取成功版本；独立请求代号确保快速切换时最后一次选择生效。 */
  async function viewVersion(id: string) {
    previewRequest.current?.abort();
    const controller = new AbortController();
    previewRequest.current = controller;
    const key = latest.current.key;
    publish({ navigation: null, previewError: "", previewLoading: false });
    if (id === latest.current.version?.id) {
      publish({ previewVersion: null });
      return;
    }
    publish({ previewLoading: true });
    try {
      const version = await api.version(id, controller.signal);
      if (!current(key, controller.signal)) return;
      if (version.id !== id || version.project_id !== latest.current.workId)
        throw new Error("返回版本与当前选择不匹配。");
      publish({
        previewVersion:
          version.id === latest.current.version?.id ? null : version,
      });
    } catch (error) {
      if (current(key, controller.signal))
        publish({
          previewError:
            error instanceof Error
              ? error.message
              : "历史版本读取失败，请重试。",
        });
    } finally {
      if (current(key, controller.signal)) publish({ previewLoading: false });
    }
  }
  /** 选择版本不改变编辑基线；未保存参数沿用同一套保存、放弃、取消保护。 */
  function selectVersion(id: string) {
    const s = latest.current;
    if (s.busy || s.loading || s.retryMode === "read") return;
    if (
      !s.previewLoading &&
      !s.previewError &&
      id === (s.previewVersion ?? s.version)?.id
    )
      return;
    if (dirty()) publish({ navigation: { version: id, saving: false } });
    else void viewVersion(id);
  }
  /** 导航目标可为作品或只读版本；保存成功后才执行，失败保持原界面。 */
  function completeNavigation(navigation: NonNullable<Session["navigation"]>) {
    if ("work" in navigation) switchWork(navigation.work);
    else {
      discardParameters();
      void viewVersion(navigation.version);
    }
  }
  /** 保存后等待成功版本到达再切换；失败留在原会话，取消只关闭对话框。 */
  function resolveNavigation(choice: "save" | "discard" | "cancel") {
    const navigation = latest.current.navigation;
    if (!navigation || latest.current.busy || latest.current.loading) return;
    if (choice === "cancel") publish({ navigation: null });
    else if (choice === "discard") completeNavigation(navigation);
    else {
      publish({ navigation: { ...navigation, saving: true } });
      if (!saveParameters())
        publish({ navigation: { ...navigation, saving: false } });
    }
  }
  /** 用户操作仅提交一次；响应未知时提示检查历史，不以重试读取变相重做任务。 */
  async function execute(
    kind: "chat" | "parameters",
    start: () => Promise<Job>,
  ) {
    if (latest.current.busy || latest.current.loading) return;
    const key = latest.current.key;
    publish({ busy: kind, error: "", retryMode: null });
    try {
      const job = await start();
      if (!current(key)) {
        if (alive.current) changed.current();
        return;
      }
      publish({ workId: job.project_id, job });
      if (alive.current) changed.current();
      await attach(job.project_id, key);
    } catch (error) {
      if (current(key))
        publish({
          busy: null,
          error: `${error instanceof Error ? error.message : "请求未完成。"} 请先检查历史会话，确认任务是否已创建。`,
          retryMode: latest.current.workId ? "read" : null,
          navigation: latest.current.navigation
            ? { ...latest.current.navigation, saving: false }
            : null,
        });
      if (alive.current) changed.current();
    }
  }
  /** 首轮图片可上传；后续输入绑定最近成功版本，澄清明确绑定提问任务。 */
  function send(text: string, image?: File) {
    const s = latest.current;
    if (
      s.busy ||
      s.loading ||
      s.previewLoading ||
      s.previewVersion ||
      dirty() ||
      (!text.trim() && !image)
    )
      return;
    const configuration = resolveComposition(s.compositionDraft);
    if (!s.workId && configuration.error) {
      publish({ error: configuration.error });
      return;
    }
    publish({
      messages: [
        ...s.messages,
        { id: crypto.randomUUID(), role: "user", text: text.trim(), image },
      ],
    });
    void execute("chat", async () => {
      if (!s.workId) {
        const asset = image ? await api.upload(image) : undefined;
        if (!current(s.key)) throw new DOMException("Aborted", "AbortError");
        return (await api.create(text, asset?.id, configuration.composition!))
          .job;
      }
      return api.message(s.workId, {
        instruction: text.trim(),
        ...(s.job?.status === "needs_input"
          ? { reply_to_job_id: s.job.id }
          : { base_version_id: s.version?.id }),
      });
    });
  }
  /** 配置只影响尚未创建的会话；同步守卫阻止上传中或切换历史时的迟到修改。 */
  function configure(compositionDraft: CompositionDraft) {
    const s = latest.current;
    if (s.workId || s.busy || s.loading) return;
    publish({ compositionDraft });
  }
  /** 尚未验收的参数与成功默认值不同，提交和复制维持锁定。 */
  function dirty() {
    return (
      !!latest.current.version &&
      !sameValues(
        latest.current.values,
        latest.current.version.candidate.default_config,
      )
    );
  }
  /** 合法参数仅更新本地预览；连续调整不提交任务，也不锁住下一次编辑。 */
  function change(name: string, value: Scalar) {
    const s = latest.current;
    if (
      !s.version ||
      !s.workId ||
      s.previewVersion ||
      s.previewLoading ||
      s.busy ||
      s.loading ||
      s.retryMode === "read" ||
      s.job?.status === "needs_input"
    )
      return;
    const control = s.version.candidate.config_schema.properties[name];
    if (!control || !validValue(control, value)) return;
    parameterSave.current = false;
    const values = { ...s.values, [name]: value };
    publish({ values, error: "", retryMode: null });
  }
  /** 明确保存时提交净变化；同步 busy 守卫阻止双击和同一事件批次的重复写入。 */
  function saveParameters() {
    const s = latest.current;
    if (
      !s.version ||
      !s.workId ||
      s.previewVersion ||
      s.previewLoading ||
      !dirty() ||
      s.busy ||
      s.loading ||
      s.retryMode === "read" ||
      s.job?.status === "needs_input"
    )
      return false;
    const parameters = Object.fromEntries(
      Object.entries(s.values).filter(
        ([name, value]) => value !== s.version!.candidate.default_config[name],
      ),
    );
    parameterSave.current = true;
    void execute("parameters", () =>
      api.message(s.workId!, {
        parameters,
        base_version_id: s.version!.id,
      }),
    );
    return true;
  }
  /** 撤销仅恢复本地已保存参数，不创建任务；保存结果未知时须先读取确认。 */
  function discardParameters() {
    const s = latest.current;
    if (!s.version || s.busy || s.loading || s.retryMode === "read") return;
    parameterSave.current = false;
    publish({
      values: { ...s.version.candidate.default_config },
      error: "",
      retryMode: null,
    });
  }
  /** 停止是唯一取消服务端任务的入口；回执后恢复快照以覆盖连接暂时中断。 */
  async function stop() {
    const s = latest.current;
    if (!s.job || !s.workId || !["queued", "running"].includes(s.job.status))
      return;
    try {
      await api.cancel(s.job.id);
      if (current(s.key)) await attach(s.workId, s.key);
      if (alive.current) changed.current();
    } catch {
      if (current(s.key)) publish({ error: "停止请求未完成，请重试。" });
    }
  }
  /** 会话和产物恢复只读取；重试失败任务才创建执行，按钮明确区分。 */
  function retry() {
    const s = latest.current;
    if (
      s.busy ||
      s.loading ||
      (dirty() &&
        !(
          parameterSave.current &&
          (s.retryMode === "read" || s.versionFailure)
        ))
    )
      return;
    if (
      (s.retryMode === "read" || (!s.retryMode && s.versionFailure)) &&
      s.workId
    )
      void attach(s.workId, s.key);
    else if (s.job) void execute("chat", () => api.retry(s.job!.id));
  }
  /** 较早消息分页只合并消息，不用旧分页响应覆盖正在推进的任务或版本。 */
  async function older() {
    const s = latest.current;
    if (!s.workId || !s.nextBefore || s.olderLoading) return;
    const signal = scope.current.signal;
    publish({ olderLoading: true });
    try {
      const page = await api.session(s.workId, signal, s.nextBefore);
      if (current(s.key, signal))
        publish({
          messages: mergeMessages(page.messages, latest.current.messages),
          jobs: {
            ...Object.fromEntries(
              (page.jobs ?? []).map((job) => [job.id, job]),
            ),
            ...latest.current.jobs,
          },
          nextBefore: page.next_before,
        });
    } catch {
      if (current(s.key, signal))
        publish({ error: "更早消息加载失败，请重试。" });
    } finally {
      if (current(s.key, signal)) publish({ olderLoading: false });
    }
  }
  useEffect(() => {
    alive.current = true;
    let selected: string | null = null;
    try {
      selected = localStorage.getItem(selectedKey);
    } catch {
      /* 使用侧栏手动恢复。 */
    }
    // StrictMode 重建 effect 时，恢复被清理的订阅，不能走同会话点击的忽略分支。
    if (selected) switchWork(selected);
    return () => {
      alive.current = false;
      scope.current.abort();
      previewRequest.current?.abort();
    };
  }, []);
  return {
    ...state,
    error: state.error || state.versionFailure?.message || "",
    retryMode:
      state.retryMode ?? (state.versionFailure ? ("version" as const) : null),
    dirty:
      !!state.version &&
      !sameValues(state.values, state.version.candidate.default_config),
    send,
    configure,
    change,
    saveParameters,
    discardParameters,
    resolveNavigation,
    canRecover:
      !state.busy &&
      !state.loading &&
      (!dirty() ||
        (parameterSave.current &&
          !!(state.retryMode === "read" || state.versionFailure))),
    stop,
    retry,
    select,
    selectVersion,
    reset: () => select(null),
    older,
  };
}
