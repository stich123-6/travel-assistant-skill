# 行程助手 MCP 连接器配置

当前版本1.2.0：行程助手仍为Skill，腾讯地图、同程和滴滴的外部能力统一由MCP连接器提供，不自动回退到供应商脚本/CLI。钉钉维持原接入；手机系统日历仍待接入。

## 配置资料

| 服务 | 连接器包/资料 | 当前状态 |
| --- | --- | --- |
| 腾讯地图 | [连接器包](../soco-travel-tencentmap-mcp-v1.0.0.zip)、[源目录](../connectors/soco-travel-tencentmap-mcp/) | 已核实官方端点和能力；云端授权、调用待验证 |
| 滴滴 | [连接器包](../soco-travel-didi-mcp-v1.0.0.zip)、[接入说明](滴滴MCP接入说明.md) | 已核实官方端点和能力；云端授权、调用待验证 |
| 同程 | 需取得官方MCP地址和实际工具schema，或由平台提供对应MCP连接器 | 待接入；不编造配置、不把tc-chengxin CLI标成MCP |

每个连接器包只包含一个MCP服务。两个包都是本项目自定义适配配置，不冒充供应商官方发布资产，不含真实Key。独立连接器包不能当成普通Skill包导入SkillHub。

## 管理员配置

1. 导入[行程助手v1.2.0](../travel-assistant-v1.2.0.zip)，替换[项目指令](WorkBuddy项目指令.md)。
2. 在实际支持的管理入口安装/接入对应连接器；若走WorkBuddy平台分发，按[连接器规范](https://open.workbuddy.cn/docs/connector)提交并完成审核。本环境未代用户提交或审核。
3. 为当前用户通过宿主真实私有入口绑定本人凭证，再让目标云端任务实际获得对应MCP工具。手机端只有“连接”而没有“添加”时，先连接列表中已有的对应服务；列表中没有，则需管理员/平台完成分发，不能只上传GitHub配置就宣称已接通。
4. 分别确认目标云端中可发现地图/滴滴工具；有真实同程MCP后再补其机票/火车票能力。授权可用但新任务未启用时重新启用，不要求用户再次往聊天发送Key。

腾讯地图的Streamable HTTP模板为 `https://mcp.map.qq.com/mcp?key=${TENCENT_MAP_API_KEY}&format=0`；滴滴为 `https://mcp.didichuxing.com/mcp-servers?key=${DIDI_MCP_KEY}`。这些是无凭证模板，占位符必须由受支持的本人凭证机制绑定，不能当作已经能连接的URL。

## 云端凭证与新任务

WorkBuddy官方自填Token规范明确凭证只保存在本机、不经过云端。本项目两个包使用该公开规范，因此只代表配置材料准备就绪，不能承诺手机云端自动复用本机Key。平台云端托管授权需按实际能力接入；不能仅把配置改成server-side/gateway就冒充已经托管。

本Skill不保存Key，不从聊天或旧任务文件搬运Key，也不把凭证放进地点记忆。用户要求“一次配置、多个任务复用”时，必须验证目标云端支持本人持久授权，并在每次新任务重新取得其可用连接器。没有此能力则报告云端授权待接入，不要求成员共享管理员Key。

## 最小验收

- 云端任务A启用本人腾讯地图连接器，工具发现成功后，用一个公开地点完成只读查询。
- 新建云端任务B，不发送Key，确认能启用同一连接器并完成只读查询。否则明确未启用、授权不可见/失效、工具缺失或网络错误。
- 滴滴同样验证工具发现与公开地点查询；连接验证不创建或取消真实订单。同程待真实MCP接入后再验证所需查票能力。
- 原有“未说明出发地先问”和“参展缺名称先问”继续生效；不能因连接器检查而提前搜索并猜测展会。

已完成的本地检查仅为配置、引用和打包；真实WorkBuddy云端绑定、跨任务授权复用与业务调用尚未验证。

官方来源：[WorkBuddy连接器规范](https://open.workbuddy.cn/docs/connector)、[腾讯位置服务MCP接入说明](https://lbs.qq.com/service/MCPServer/MCPServerGuide/userGuide)、[腾讯地图能力清单](https://docs.cloudbase.net/ai/mcp/develop/server-templates/cloudrun-mcp-tencent-map)、[滴滴MCP文档](https://mcp.didichuxing.com/api)。
