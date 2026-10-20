---
name: travel-tencentmap-mcp
description: 用于通过已连接的腾讯位置服务官方MCP搜索地点、解析地址、规划驾车公交步行骑行路线，或查询距离和天气。
---

# 行程助手腾讯地图 MCP 适配

本项目自定义适配说明，调用腾讯位置服务官方MCP，不冒充官方发布的连接器。来源：[官方概述](https://lbs.qq.com/service/MCPServer/MCPServerGuide/overview)、[接入说明](https://lbs.qq.com/service/MCPServer/MCPServerGuide/userGuide)。

使用当前宿主实际提供的工具和schema；已有MCP时无需本地地图Skill或Python包。没有工具或未授权时引导真实连接器入口，不索要聊天填写Key，不从旧对话、任务文件或环境变量恢复凭证。本机自填Token不代表手机云端已授权。

- 用户没有明确本次出发地时先问；已保存家/公司地址不等于本次已选择，不用服务器IP定位用户。
- 地点解析/候选使用实际提供的`geocoder`、`placeSuggestion`等工具，同名地点交用户选择，不能编造POI ID或坐标。参展请求缺名称先问名称，不能根据附近场馆猜展会。
- 路线按schema调用`directionDriving`、`directionTransit`、`directionWalking`或`directionBicycling`；`from`/`to`常用`lat,lng`格式，必须来自真实查询。不要把旧Skill的`direction(from_addr,to_addr,mode)`当成MCP接口。
- 需要时调用实际提供的`matrix`、`placeDetail`或`weather`，日期与天气预报范围、权限配额均按返回处理。
- 输出保留真实距离、耗时、方式和查询时间，不把当前路况当成未来确定耗时。不编造路线或天气，不以规划成功代替手机日程已创建。
- 不调用购票、订单或消息工具。连接验证仅发现工具和查询公开地点。
