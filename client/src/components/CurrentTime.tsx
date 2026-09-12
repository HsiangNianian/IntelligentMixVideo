/** 页头时间卡片每秒读取本机时钟，跟随系统时区，并在卸载时清理定时器。 */
import { useEffect, useId, useState } from "react";
import { Card } from "@/components/ui/card";

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
    <section aria-labelledby={titleId} className="ml-auto shrink-0">
      <Card className="gap-1 px-4 py-3 text-right">
        <h2 id={titleId} className="text-xs font-medium text-muted-foreground">当前时间</h2>
        <time className="text-sm whitespace-nowrap tabular-nums" dateTime={now.toISOString()}>
          {formatter.format(now)}
        </time>
      </Card>
    </section>
  );
}
