# 1Panel 部署：travel-assiant-mcp.st-arn.com

部署独立服务，不改 `mcp.st-arn.com`、OneID 回调或其容器。先确认本机8030端口空闲；如占用，只改 compose.yaml 左侧宿主端口及反代端口，不改其他服务。

## 放置文件与启动

1Panel「文件」中新建独立目录，例如 `/opt/travel-assistant-mcp`，将发行包 **mcp-service 目录里的内容**放进去，compose.yaml 与 Dockerfile 同级。不要用仓库根目录当构建目录。

在该目录的服务器终端运行（Linux）：

```sh
cp .env.example .env
chmod 600 .env
docker compose build
docker compose run --rm --no-deps travel-mcp python -m travel_mcp generate-key
```

将生成的主密钥填入 `.env` 的 `TRAVEL_ENCRYPTION_KEY`，只存服务器和管理员私密备份，不发送聊天、不提交 GitHub，升级时保留原值。不能保留样例占位符。

`.env` 已配置 `PUBLIC_BASE_URL=https://travel-assiant-mcp.st-arn.com`。继续：

```sh
docker compose up -d
docker compose exec travel-mcp python -m travel_mcp create-user jane
docker compose ps
curl -fsS http://127.0.0.1:8030/health
```

交互终端输入两次密码，密码不写在命令参数、编排文件或聊天中。每位成员创建独立账号。

使用1Panel「容器→编排」时选择该目录中的 compose.yaml 和 `.env`；也可先在服务器终端构建。首次构建需下载 Python 镜像和固定版本依赖，要求服务器能访问镜像源。当前交付未在 Docker 环境实际构建。

## DNS、HTTPS、反代

1. 新子域名 DNS 指向服务器。
2. 在1Panel「网站」新建**独立反向代理网站**，域名 `travel-assiant-mcp.st-arn.com`，上游 `http://127.0.0.1:8030`。
3. 为新网站申请并开启 HTTPS 证书，保持旧 MCP 配置。
4. 按 [openresty-location.conf](openresty-location.conf)核对 Host、完整路径转发、超时和关闭缓冲。已有 location / 时编辑现有块，不重复添加。
5. 使用面板检查配置，通过后重载。

模板假设 OpenResty 可访问宿主回环地址。如果 OpenResty 使用隔离网络，需配置同一私有网络或可达的宿主地址，不要直接把8030暴露公网。

## 上线后的最小检查

```sh
curl -fsS https://travel-assiant-mcp.st-arn.com/health
curl -fsS https://travel-assiant-mcp.st-arn.com/.well-known/oauth-authorization-server
curl -fsS https://travel-assiant-mcp.st-arn.com/.well-known/oauth-protected-resource
curl -i https://travel-assiant-mcp.st-arn.com/mcp
```

health 应为 ok；issuer/resource 均为新域名；未携带凭证访问 `/mcp` 应返回401。浏览器打开 `https://travel-assiant-mcp.st-arn.com/account` 登录，保存本人的腾讯地图／滴滴 Key，页面不得回显 Key。

随后将独立连接器接入 WorkBuddy。平台使用云端 HTTPS 回调时，取得准确来源后填写 `OAUTH_REDIRECT_ORIGINS=https://平台实际回调域名` 并重新加载环境：

```sh
docker compose up -d --force-recreate
```

不填写未知域名或 `*`，平台托管 OAuth 仍需协调。连接后先验证地点记忆和地图查询，再在手机两个云端新任务验证复用。真实叫车仅在本人确认当前报价后测试，结果不明先查订单，禁止自动再下单。

## 升级与备份

SQLite与加密凭证在命名卷 `travel-data`。升级保留此卷、compose项目名称和 `.env` 原主密钥：

```sh
docker compose up -d --build
```

不要执行 `docker compose down -v`。更换编排名或目录前确认复用原卷，否则可能创建空卷。

用1Panel备份命名卷与 `.env` 到团队受控的私有位置。为获得一致的文件备份，安排短暂停服后备份再启动，不直接把运行中的数据库复制当作可靠备份。恢复需同时具备数据卷和原主密钥，丢失主密钥无法解密已保存凭证。
