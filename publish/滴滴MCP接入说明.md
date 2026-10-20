# 滴滴 MCP 接入说明

按用户提供的[滴滴官方仓库](https://github.com/didi/didi-ride-skill)准备 WorkBuddy 自定义连接器配置；行程助手当前v1.2.0统一使用出行服务MCP，不自动回退到供应商CLI。连接器配置版本仍为1.0.0，由本项目编写，不冒充官方发布资产。

## 已准备的内容

- `connectors/soco-travel-didi-mcp/`：包含元信息、`mcp.json`、密码输入表单、图标和独立调用说明。
- [连接器包](../soco-travel-didi-mcp-v1.0.0.zip)：供支持该格式的管理入口导入，或提交 WorkBuddy 开放平台审核；不当作普通 Skill 包导入。
- [新版行程助手](../travel-assistant-v1.2.0.zip)：使用宿主当前任务可用的滴滴MCP，不以OpenClaw/mcporter为前置条件；统一部署见[MCP连接器配置](MCP连接器配置.md)。

上述文件不含真实 Key，没有绑定用户的 WorkBuddy 账号，也没有完成真实授权或叫车联调。

## 连接方式

实际 MCP 地址模板为 `https://mcp.didichuxing.com/mcp-servers?key=${DIDI_MCP_KEY}`，WorkBuddy 配置类型为 `streamableHttp`。这是远程 MCP，无需自建滴滴服务器；GitHub 仓库本身不是 MCP 服务地址。

1. 在[滴滴官方平台](https://mcp.didichuxing.com/claw)登录本人账号并激活 MCP Key。
2. 若当前 WorkBuddy 管理界面支持添加自定义 MCP，使用其真实配置入口导入 `mcp.json` 并通过受支持的本人凭证机制绑定 `DIDI_MCP_KEY`。占位符不是可用 Key；不能只粘贴模板后宣称连接成功。
3. 若需走平台连接器分发，按[WorkBuddy 开放平台连接器规范](https://open.workbuddy.cn/docs/connector)提交独立连接器包并完成审核。不要把连接器 ZIP 上传为 SkillHub 的普通 Skill，或擅自冒充供应商官方连接器。
4. 连接器在当前任务实际可用后，将其绑定到行程助手项目；替换新版 Skill 与项目指令，在新任务验证。

## 手机云上模式的限制

WorkBuddy 官方文档明确：`auth_mode: token` 收集的凭证只保存在用户本机、不经过云端。本包使用该已公开的表单规范，**不能保证该本机凭证可用于手机云端**。用户需要纯云端运行，必须先确认当前账号的云端自定义 MCP 能否安全绑定本人 Key；没有入口时，状态保持“云端授权待接入”。不能通过把个人 Key 写进公开配置、共享项目或聊天来绕过。

WorkBuddy 文档将云端托管 OAuth 指向 `server-side`/`gateway`，要求接入前与平台团队确认。本项目未获得此托管能力，也没有滴滴 OAuth 配置，不能只修改一个字段就声称云端托管授权已实现。电脑端连接成功与手机云端连接成功分开报告；电脑远程模式可作为用户另行选择的运行方式，不自动替换云上模式。

## 最小验证

先在目标执行端完成工具发现或 `tools/list`，再用公开地点调用 `maps_textsearch`，确认返回真实结果；需要查价时再使用本次用户确认的起终点调用 `taxi_estimate`。不为验证连接创建、取消订单。

开发时可以按[滴滴 API 文档](https://mcp.didichuxing.com/api)使用 `mcp-servers-sandbox` 调试端点；模拟结果不能用于实际行程。生产端点可能产生真实订单及费用，下单前展示车型价格并取得对应明确同意。

本地仅检查 JSON、占位符与表单键对应、HTTPS 官方端点、文件引用和 ZIP 一致性；未完成 WorkBuddy 导入审核、云端凭证绑定或真实业务调用。
