import { useEffect, useState } from "react";

const formatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export default function CurrentTime() {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <section className="clock" aria-labelledby="clock-title">
      <h1 id="clock-title">当前时间</h1>
      <time dateTime={now.toISOString()}>{formatter.format(now)}</time>
    </section>
  );
}
