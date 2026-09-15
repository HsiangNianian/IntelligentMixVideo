/** 编译真实 API 模块验证默认端口及环境覆盖；执行 bun run test，不访问网络。 */
import { expect, test } from "bun:test";

// 未配置、空值与空白均连接服务默认端口；显式地址保留部署前缀并去除尾斜线。
test.each([
  [undefined, "http://localhost:20070"],
  ["", "http://localhost:20070"],
  ["  ", "http://localhost:20070"],
  [" https://api.example.test/custom/// ", "https://api.example.test/custom"],
])("Remotion API 地址配置 %j", async (configured, expected) => {
  const build = await Bun.build({
    entrypoints: [new URL("../src/features/remotion_templates/api.ts", import.meta.url).pathname],
    target: "bun",
    define: { "import.meta.env.VITE_API_URL": JSON.stringify(configured) ?? "undefined" },
  });
  expect(build.success).toBe(true);
  const source = await build.outputs[0].text();
  const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
  expect(api.apiUrl("/works")).toBe(`${expected}/api/templates/works`);
});
