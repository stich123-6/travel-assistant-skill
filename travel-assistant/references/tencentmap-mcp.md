# 腾讯地图 MCP

官方来源：[概述与能力](https://lbs.qq.com/service/MCPServer/MCPServerGuide/overview)、[接入说明](https://lbs.qq.com/service/MCPServer/MCPServerGuide/userGuide)。2026-10-20核实远程接入地址；每次调用仍以实际 schema 为准。

WorkBuddy Streamable HTTP 配置模板：`https://mcp.map.qq.com/mcp?key=${TENCENT_MAP_API_KEY}&format=0`，类型为 `streamableHttp`。官方也提供 `/sse` 接入方式；不要在连接失败时自行改协议来绕过授权。Key 需具有所用 WebService API 的权限和配额，由宿主的本人凭证入口配置。

## 工具与参数

MCP 工具与旧地图 Skill 的 Python 方法不同。先检查当前 schema，以下是官方文档的能力映射，不能擅自换回 `poi_search` 或 `direction(from_addr, to_addr, mode)`：

| 需求 | MCP 工具示例 | 处理 |
| --- | --- | --- |
| 地址解析 | `geocoder` | 传含城市的 `address`，使用真实结果中的经纬度 |
| 地点候选 | `placeSuggestion` | 按 schema传 `keyword`、适用的 `region`，同名候选交用户选择 |
| 地点详情 | `placeDetail` | 使用查询返回的真实 POI ID |
| 驾车、公交、步行、骑行 | `directionDriving`、`directionTransit`、`directionWalking`、`directionBicycling` | 先定位起终点，`from`/`to`通常为 `lat,lng`；日期时间、偏好按当前 schema支持的字段传入 |
| 距离矩阵 | `matrix` | 使用真实坐标和当前 schema允许的方式 |
| 天气 | `weather` | 使用真实行政区划编码与查询类型，区分实时和预报 |

坐标格式严格按各工具 schema：腾讯地图路线常用纬度在前，滴滴某些组合字段为经度在前，不能原样互换。外部坐标需在有实际转换能力时转换，不能假设GPS或其他地图坐标已经是腾讯坐标。

没有明确本次出发地先问，不能用服务器IP推断用户所在位置。活动场馆不明确时按主Skill的展会名称确认流程处理，不用地图候选代替展会身份确认。

路线结果保留真实距离、耗时、方式、查询时间和适用日期；未来行程未提供预测能力时标明估计依据。失败或缺少工具时不编造路线、天气或出发时刻。地图指南等旧Skill扩展能力只有当前MCP实际提供时才可调用。

原生MCP工具可用时无需本地 `tencentmap-map-assistant` 或安装 Python 包。凭证管理和新任务验证按[MCP 运行约定](mcp-connectors.md)执行。
