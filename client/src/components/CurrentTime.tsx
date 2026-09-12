/** 当前时间卡片每秒读取本机时钟，按本机时区显示，并在卸载时清理定时器。 */
import { useEffect, useId, useState } from "react";
import { Card } from "@/components/ui/card";

/** 复用中文 24 小时制格式器；不指定时区以跟随系统设置。 */
const formatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** 封装计时状态和语义化时间输出，避免计时器进入页面布局。 */
export default function CurrentTime() {
  const titleId = useId();
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <section aria-labelledby={titleId} className="w-full max-w-xl">
      <Card className="items-center px-6 text-center">
        <h1 id={titleId} className="text-lg font-medium text-muted-foreground">当前时间</h1>
        <time className="text-[clamp(1.25rem,5vw,2.5rem)] tabular-nums" dateTime={now.toISOString()}>
          {formatter.format(now)}
        </time>
      </Card>
    </section>
  );
}
