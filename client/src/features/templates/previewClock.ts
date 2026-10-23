/**
 * 模板预览的前端时钟与画面来源的公共接口。IMS 播放器还没就绪（加载、应用修改中或已失败）且没有可用的基础视频时，
 * 由前端时钟按真实经过的时间推进全局播放位置，播放按钮、进度条、时间轴、文字选择框与 AI 资产照常可用；
 * 它没有画面，只负责时间，回调与基础视频相同，预览换用其他画面来源时交接位置。
 */

/** 预览主控件操作画面来源（IMS 播放器、基础视频或前端时钟）的入口；时间都是模板全局时间（秒）。 */
export interface PlaybackHandle {
  /** 从 from 开始播放；省略时从当前位置继续，已到结尾则从头播放。 */
  play(from?: number): void;
  pause(): void;
  /** 暂停并定位到 time。 */
  seek(time: number): void;
}

/** 时钟回报给预览的回调。 */
interface ClockCallbacks {
  /** 模板总时长（秒），播放到它时停下。 */
  duration(): number;
  /** 播放时每帧回报一次，定位时立即回报。 */
  onTime(time: number): void;
  onPlayingChange(playing: boolean): void;
  onEnd(): void;
}

/** 按 requestAnimationFrame 逐帧回报、按 performance.now() 计算位置的播放时钟。 */
export class PreviewClock implements PlaybackHandle {
  private position = 0;
  // 播放中记录开始推进时的位置与时刻，暂停时为 null。
  private origin: { time: number; at: number } | null = null;
  private frame: number | undefined;

  constructor(private readonly callbacks: ClockCallbacks) {}

  /** 当前位置：播放中按经过的真实时间推进，不超过总时长。 */
  private current() {
    return this.origin ? Math.min(this.callbacks.duration(), this.origin.time + (performance.now() - this.origin.at) / 1000) : this.position;
  }

  play(from?: number) {
    const start = from ?? (this.current() >= this.callbacks.duration() - 0.001 ? 0 : undefined);
    if (start !== undefined) this.seek(start);
    if (this.origin) return;
    this.origin = { time: this.position, at: performance.now() };
    this.callbacks.onPlayingChange(true);
    this.frame = requestAnimationFrame(this.tick);
  }

  pause() {
    this.stop(true);
  }

  seek(time: number) {
    if (!Number.isFinite(time)) return;
    this.stop(true);
    this.position = Math.min(this.callbacks.duration(), Math.max(0, time));
    this.callbacks.onTime(this.position);
  }

  /** 停止推进并记下位置；notify 时通知主控件已暂停。 */
  private stop(notify: boolean) {
    if (this.frame !== undefined) cancelAnimationFrame(this.frame);
    this.frame = undefined;
    if (!this.origin) return;
    this.position = this.current();
    this.origin = null;
    if (notify) this.callbacks.onPlayingChange(false);
  }

  /** 逐帧回报位置，到结尾时停在总时长并通知播放结束。 */
  private readonly tick = () => {
    this.frame = undefined;
    const time = this.current();
    this.callbacks.onTime(time);
    if (time < this.callbacks.duration()) this.frame = requestAnimationFrame(this.tick);
    else {
      this.stop(false);
      this.callbacks.onEnd();
    }
  };
}
