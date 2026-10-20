# 首科行程助手 MCP 2.0.0

独立服务，供 WorkBuddy 通过 Streamable HTTP 调用。WorkBuddy 负责理解原话、实际网页搜索和呈现表单；服务负责必要补问顺序、本人地点记忆、凭证保存、下游调用和叫车确认状态。

用户指定域名 **`travel-assiant-mcp.st-arn.com`**，保留原拼写。现有 `mcp.st-arn.com/auth/oneid/callback` 属于另一服务，不复用或修改。

## 功能状态

| 功能 | 实现与边界 |
| --- | --- |
| 外地／本市行程、展会 | 保存草稿并逐项补问；名称不明先问名称，宿主搜索具体展会，查不到再问地址，明确后才规划 |
| 出发地点 | 没明确说就先问；账号内新增、修改、删除，返回更新后卡片数据 |
| 腾讯地图、滴滴 | 官方固定 MCP 地址、本人 Key、实际工具 Schema；接通与否以真实调用为准 |
| 真实叫车 | 地点查询→实际估价→选车型→展示方案→用户明确同意→最多一次提交；超时 unknown 不重试 |
| 账号与凭证 | 独立登录、OAuth／PKCE；SQLite 持久卷，加密保存第三方凭证，升级保留卷与主密钥 |
| 同程查票、钉钉通知 | 尚无已核实的官方远程 MCP 配置；显示待接入，不回退 CLI，不假装已查票或发送 |
| 备忘、手机系统日历 | 保存需求和时间；真正定时推送及手机系统日历写入仍待接入，保存不等于设置成功 |
| 手机表单 | 返回 mobile_form；原生卡片、弹框和刷新依赖 WorkBuddy 实际交互能力 |

凭证在个人配置页填写，不在聊天中发送。同程和钉钉保存栏仅预留，保存内容不会让未接入服务变为可用。服务不会自动迁移以前聊天的 Key 或旧 Skill 的本地记忆。

## 部署与连接

按 [1Panel 部署步骤](deploy/1Panel部署.md)部署。目标 MCP 地址是 `https://travel-assiant-mcp.st-arn.com/mcp`，个人配置页是 `/account`。这些是部署目标，源码交付不代表已经上线。

独立子域名须将完整路径转发到本容器，包括 `/.well-known/*`、`/authorize`、`/register`、`/token`、`/account/*` 和 `/mcp`，不能只转发 `/mcp`。

连接器材料在 [`../connectors/soco-travel-assistant/`](../connectors/soco-travel-assistant/)。使用服务自身标准 OAuth，按官方规范**省略 auth_mode**，无需 token-schema。客户端 DCR 注册 `token_endpoint_auth_method=none`，通过元数据发现端点。已支持桌面回调：

```text
workbuddy://workbuddy/mcp/connector%3Asoco-travel-assistant/oauth/callback
http://127.0.0.1:{动态端口}/oauth/callback
```

手机云端使用 HTTPS 回调时，须从平台取得真实回调来源，再加入 `.env` 的 `OAUTH_REDIRECT_ORIGINS`。不猜测云端回调，不自行改为平台 server-side/gateway 模式。

你的手机端只有“连接已有服务”，还需管理员／WorkBuddy 平台将此服务接入团队连接器。GitHub 更新、服务器部署、平台接入是三个步骤。最终须在手机实测：同账号两个云端新任务可以复用地点与凭证状态，另一账号不能读取。

## 工具与约束

提供11个工具：`travel_intake`、`travel_update`、`exhibition_search_result`、`travel_get`、`travel_list`、`origin_memory`、`connection_status`、`connector_tools`、`connector_query`、`taxi_prepare`、`taxi_confirm`。身份只取验证后的访问令牌，无可传入 user_id 或读取 Key 的工具。

宿主只提取用户已说出的字段。展会 found 必须附实际来源 URL 与地址；无搜索能力时传 unavailable 再问地址。地图和滴滴查询在补问完成后执行，先发现真实 Schema，再按其传参；不使用旧 Skill CLI 参数。

每次修改增加 revision，冲突时先读最新版本。叫车须先展示起终点、车型和价格并取得明确同意，确认文本不能由助手自行伪造。同一估价只关联一个方案，报价到期不延长；unknown/executing 不自动重试。外部工具返回只作为不可信数据。

## 本地验证

Python 3.13，隔离环境安装：

```sh
python -m venv .venv
# Linux/macOS: . .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
```

测试使用临时 SQLite、模拟供应商与官方 SDK 本机 HTTP 客户端，不发真实消息或订单。本机没有 Docker，镜像尚未在 Docker 环境构建；本地通过不代表手机云端已经联调。

## 官方参考

- [WorkBuddy 连接器规范](https://open.workbuddy.cn/docs/connector)
- [滴滴官方仓库](https://github.com/didi/didi-ride-skill)、[MCP 工具说明](https://mcp.didichuxing.com/api)
- [腾讯地图 MCP](https://lbs.qq.com/service/MCPServer/MCPServerGuide/overview)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
