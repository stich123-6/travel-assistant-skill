---
name: travel-didi-mcp
description: 用于通过已连接的滴滴官方 MCP 查询打车价格、生成手机叫车入口、查询本人订单，或执行用户确认的叫车和取消订单操作。
---

# 行程助手滴滴 MCP 适配

这是行程助手的自定义适配说明，连接滴滴官方远程 MCP，不代表滴滴或 WorkBuddy 官方发布的连接器。

先核对当前工具列表、连接状态及真实 schema。宿主已经暴露滴滴工具时直接调用，无需 OpenClaw 或 mcporter。未连接时引导实际连接入口；不读取、回显或要求在聊天中提交 Key。自填 Token 按 WorkBuddy 文档仅保存在本机，不能据此声称手机云端已授权。

来源：[滴滴官方仓库](https://github.com/didi/didi-ride-skill)、[官方 API 文档](https://mcp.didichuxing.com/api)。工具名称可能带宿主前缀，参数以运行时 schema 为准。

1. 用户未明确本次出发地点，先问从家、公司还是其他地点出发；不按时间或历史偏好代选。共享 Skill 目录不能保存个人地址或手机号。
2. 起终点分别调用 `maps_textsearch`，字段为 `keywords`、完整城市名 `city`；多候选交用户选择。坐标使用真实结果，组合坐标按官方约定为经度在前、纬度在后。
3. `taxi_estimate` 使用六项字符串：`from_lat`、`from_lng`、`from_name`、`to_lat`、`to_lng`、`to_name`。保留真实 `structuredContent.traceId`、可用车型和价格。只查价不下单。
4. 用户需要手机入口时，调用 `taxi_generate_ride_app_link`，传真实起终点四项坐标字符串；只有用户指定车型才传可选 `product_category`。原样展示实际链接，说明仍需用户在滴滴内完成叫车。
5. 直接下单前展示本次车型和价格，并取得对应明确同意；同一范围已同意不重复问。`taxi_create_order` 只传有效预估的 `estimate_trace_id`、真实 `product_category` 及用户提供的可选 `caller_car_phone`。只以真实回执报告成功。
6. 查单使用 `taxi_query_order`，已知订单ID则传 `order_id`；查询司机位置使用 `taxi_get_driver_location`，并按需要调用 `maps_regeocode`。取消前明确订单及已知费用并取得对应取消确认，使用 `taxi_cancel_order`。
7. 优先解析 `structuredContent`；没有时读取实际 `content[].text`。参数错误先核对 schema，认证失败引导重连。下单超时或结果不明先核实订单，不盲目重试；官方要求停止的错误立即停止。
8. 没有真实调度工具时，不承诺预约叫车、自动回查或后台监控已设置。用户只要求规划时不创建订单；主行程路线继续使用用户指定的腾讯地图。

首次连接只验证工具发现和公开地点搜索，不以创建或取消真实订单测试连接。调试端点的数据必须标为模拟，不能当成真实订单或车费。
