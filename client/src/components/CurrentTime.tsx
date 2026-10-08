/** 页头时间胶囊每秒读取本机时钟，跟随系统时区，并在卸载时清理定时器。 */
import { useEffect, useId, useState } from "react";
import { Clock } from "lucide-react";

/** 复用中文 24 小时制格式器；省略时区以跟随系统设置。 */
const formatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** 独立管理计时状态，避免每秒刷新整个模板工作区。 */
export default function CurrentTime() {
  const titleId = useId();
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <section aria-labelledby={titleId} className="ml-auto flex h-8 shrink-0 items-center gap-2 rounded-full border bg-card px-3 text-muted-foreground">
      <Clock className="size-3.5" aria-hidden="true" />
      <h2 id={titleId} className="sr-only">当前时间</h2>
      <time className="font-mono text-xs whitespace-nowrap text-foreground/85 tabular-nums" dateTime={now.toISOString()}>
        {formatter.format(now)}
      </time>
    </section>
  );
}
