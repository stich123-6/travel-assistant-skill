# 行程助手独立 MCP 服务：设计与执行计划

用户已选择把行程助手本身做成独立MCP服务，替代Skill；原 `https://mcp.st-arn.com/auth/oneid/callback` 属于另一服务，保持现状。用户确认新域名为 `travel-assiant-mcp.st-arn.com`，通过1Panel管理，已有Docker；尚未取得服务器操作入口，尚未实际部署。

## 交付范围

- Python MCP SDK 2.3.0，提供Streamable HTTP `/mcp`、健康检查和个人配置页面；Docker部署。
- 服务端持久保存每个用户的地点、行程、备忘与授权状态，SQLite文件放持久卷。第三方Key经Fernet加密，主密钥只在服务器环境中配置；不提供经MCP读取/填写Key的工具。
- MCP端使用OAuth 2.1/PKCE与独立用户登录，令牌与用户关联、刷新后持续访问本人数据。用户由管理员命令创建，不开放匿名自注册。账号授权可以跨WorkBuddy新任务复用，需目标云端实测，不声称代码交付等于已接通。
- WorkBuddy仍负责自然语言推理、调用工具和展示卡片；服务返回意图、补问与下一步。具体展会名称缺失先问名称，有名称再要求针对名称搜索，查不到再问地址，明确地点后才能查路线。外地、本市、备忘和钉钉通知四类流程均有状态表示。
- 只读下游MCP：腾讯地图、滴滴为已核实端点；同程与钉钉需管理员提供可验证MCP配置，没有则待接入。不编造API，不以记录代替消息/订单/提醒成功。
- 真实写入与订单先形成具体待确认动作，按当前用户和方案版本核对同意；超时标记unknown，不自动重试。手机系统日历与真正定时调度继续待接入。
- 不改另一MCP的回调、DNS或现有服务。正式部署需要域名/路由与服务器访问方式；默认Docker在127.0.0.1:8030，由反向代理提供HTTPS。

## 文件和接口

`mcp-service/travel_mcp/`：`store.py`负责SQLite/加密/用户隔离，`workflow.py`负责补问和行程状态，`auth.py`负责OAuth及登录/凭证页面，`server.py`负责MCP工具，`upstream.py`负责官方下游MCP，`config.py`负责环境配置；测试在`mcp-service/tests/`。

存储接口：`Store(data_dir: Path, encryption_key: str)`；`connection()`返回sqlite连接上下文；`create_user(username,password)->dict{id,username}`、`authenticate(username,password)->str|None`、`get_user(user_id)`；`set_credentials(user_id,provider,values:dict)`、`get_credentials(user_id,provider)->dict|None`、`credential_status(user_id)->dict`、`delete_credentials(user_id,provider)`；`get_record(user_id,record_id)`、`save_record(user_id,record:dict,expected_revision:int|None)`、`list_records(user_id)`；`origin(user_id,action,label=None,address=None,origin_id=None,expected_revision=None)->dict`。

工作流接口：`TravelService(store)`；`intake(user_id,message,fields=None)`、`update(user_id,record_id,patch,expected_revision)`、`record_search(user_id,record_id,status,venue=None,source_url=None,expected_revision=None)`。所有返回为JSON可序列化字典；业务错误使用`ServiceError(code,message)`，MCP层不回传异常堆栈。

## 执行顺序与最小验证

1. 先写并运行持久化/补问测试观察失败，再实现存储和工作流；测试展会先问、搜索失败补地址、地点卡片更新、跨用户访问阻断、重启后保留与密文落盘。
2. 使用官方SDK实现认证传输及个人凭证页面；测试未授权401、PKCE失败、授权码一次性、用户隔离、页面CSRF及刷新令牌。只检查本次认证和输入边界。
3. 加入MCP工具、下游只读调用和写动作门禁；使用模拟MCP返回测参数、无凭证待接入、订单超时不重试，再以真实SDK客户端完成本地协议连接与tools/list/call。
4. 生成Docker/反代配置、连接器元信息和部署文档；运行受影响的最小测试及本地HTTP验证，核对无真实凭证入包，再同步GitHub。云端部署、平台上架和真实业务调用分别报告。

## 执行约束与记录

用户要求快速执行、梳理后直接继续；本文件作为本次可审阅的设计与计划，不另设重复批准环节。按相关技能将存储/工作流、认证传输分开实现，最后做一次只针对本次变更的独立审查；不做全仓库扫描。已有Skill保留为历史包，新服务为主交付。

## 本次执行结果

- 已实现独立服务、11个MCP工具、OAuth/PKCE、个人凭证页、账号地点与行程持久化、腾讯地图/滴滴官方端点调用、叫车确认门禁及1Panel部署材料。
- 已运行37项相关测试，全部通过，包含官方SDK客户端通过本机真实HTTP完成初始化、工具发现和调用；第三方业务使用模拟返回，未调用真实订单或消息。
- 本次独立审查发现并修复两个叫车门禁问题：同一估价的重复方案、prepare延长报价期限。数据库唯一索引确保同一用户与traceId只生成一个方案，期限绑定原估价；定向复核通过。
- 当前环境无Docker，尚未构建容器、登录用户1Panel、配置DNS/HTTPS、部署服务器或与WorkBuddy手机云端联调。同程/钉钉远程接口、定时调度及手机系统日历仍待接入。
