# 滴滴官方 MCP 接入

用于打车查价、手机跳转入口、用户已确认的下单和查单。主行程路线仍使用用户指定的腾讯地图。

官方来源：[滴滴仓库](https://github.com/didi/didi-ride-skill)、[MCP 开发文档](https://mcp.didichuxing.com/api)。接入方式核对日期：2026-10-20；具体工具始终以运行时 schema 为准。

## 选择执行方式

1. 检查宿主当前可用工具和连接状态。若已暴露滴滴的 `maps_textsearch`、`taxi_estimate` 等，直接调用相应工具；名称可能带宿主前缀。该路径无需读取或输出 Key，也无需本地 OpenClaw/mcporter。
2. 有连接器但未启用，按宿主实际入口启用；未授权则提供实际授权入口。仅知道服务名称或仓库地址不能证明当前连接成功。
3. 没有原生 MCP 时，检查已安装的 `didi-ride-skill` 能否在当前执行环境运行；遵循其实际安装说明。不要在缺少工具时假造调用，也不要把电脑本地的授权当作云端已有授权。
4. 没有可用执行路径时，只暂停滴滴相关动作，保留已确认的行程和准备清单，说明具体缺项。地图或票务各自独立判断，不要求先连接全部服务。

## MCP 配置与凭证

- 传输：Streamable HTTP。在 WorkBuddy 的 `mcp.json` 中写为 `streamableHttp`。
- 生产端点模板：`https://mcp.didichuxing.com/mcp-servers?key=${DIDI_MCP_KEY}`。
- 官方调试端点模板：`https://mcp.didichuxing.com/mcp-servers-sandbox?key=${DIDI_MCP_KEY}`；返回 Mock 数据，不代表真实资源或真实订单。
- 本人 Key 从[滴滴官方平台](https://mcp.didichuxing.com/claw)激活，交给宿主凭证表单或其已支持的私有凭证机制注入。安装包、聊天、行程记录、项目共享资产和 GitHub 不保存真实 Key；错误输出隐藏含 Key 的 URL。
- WorkBuddy 自填 Token 连接器要求 `auth_mode: token`、密码类型表单和相同名称的占位符。**官方文档说明该模式凭证只保存在本机、不经过云端。** 因此生成配置、导入成功或电脑授权都不能标记手机云端已接通。云端需确认宿主实际支持的本人凭证注入；没有入口时报告云端授权待接入，不要求用户把 Key 粘贴到对话。
- 云端托管授权需按 WorkBuddy 平台实际支持接入，不能只把 `auth_mode` 改成 `server-side` 或 `gateway` 就声称完成。

## 按工具执行

| 动作 | 必须保留的约定 |
| --- | --- |
| `maps_textsearch` | `city` 使用完整城市名；`keywords` 为具体地点。起终点各自查询；多个有效候选让用户选择。 |
| `taxi_estimate` | 六项字符串：`from_lat`、`from_lng`、`from_name`、`to_lat`、`to_lng`、`to_name`；坐标来自本次实际查询。 |
| `taxi_generate_ride_app_link` | 使用真实起终点四项坐标字符串；仅用户指定车型时传可选 `product_category`。返回入口不等于下单。 |
| `taxi_create_order` | 用有效预估的 `traceId` 作 `estimate_trace_id`，用返回车型作 `product_category`；可选 `caller_car_phone`。先展示车型价格并取得对应下单同意。 |
| `taxi_query_order` | 有真实订单ID则传 `order_id`；无ID时仅按当前 schema 支持查询本人当前订单。查询不能替代创建回执。 |
| `taxi_cancel_order` | 明确订单，展示本次取消对象与已知费用信息，并取得对应取消确认；已有同一范围的确认不重复问。 |

没有明确本次出发地仍先询问，不能照搬上游按早晚推断家或公司的策略。成员地址偏好只使用本项目的本人私有记忆，不能写入共享 Skill 的 `assets/PREFERENCE.md`。

优先解析响应 `structuredContent`；没有时解析实际 `content[].text`。不把示例坐标、车型代码、traceId、价格或链接当成真实结果。认证失败引导重连；参数错误核对 schema；下单超时或结果未知先查订单，禁止盲目重试。官方明确要求停止的下单错误停止处理，不自动改用另一条下单路径。

预约与自动查单需要真实调度工具和相应同意；没有调度则说明未安排自动执行，不能假定有 OpenClaw cron。

## 首次连接验证

先以宿主的工具发现或 `tools/list` 检查连通性，再用一个公开地点做 `maps_textsearch` 只读查询，确认当前执行端确实收到结果。需要查价时才按用户本次确认的地点预估。验证连接不创建、取消订单，也不调用消息发送。仅完成静态配置检查时报告“配置已准备，尚未完成授权联调”。
