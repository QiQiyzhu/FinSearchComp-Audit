# 联网研究后端部署

GitHub Pages 保留公开研究工作台。联网研究需要独立 HTTPS Python 服务：服务端持有 DeepSeek 密钥，接受问题后返回任务编号，再由网页轮询进度与报告。静态网页不会内嵌模型密钥。

## 免费演示服务：Render Blueprint

[使用本仓库在 Render 部署](https://render.com/deploy?repo=https://github.com/QiQiyzhu/FinSearchComp-Audit)。根目录 `render.yaml` 明确使用 **free** Web 方案、**free** Key Value 预算存储、单个应用容器和 `/api/health` 健康检查；不会创建付费数据库或磁盘。需要先登录自己的 Render 账户。密钥仅在 Render 的环境变量设置中填写。

1. 在 Blueprint 创建页确认仓库与 `free` 服务计划。
2. 填写 `DEEPSEEK_API_KEY` 和有效的 `SEC_USER_AGENT`。模型默认 `deepseek-flash`，可在服务环境变量中改为账户实际可用型号。
3. 等服务变成 Live，复制 Render 分配的 HTTPS 地址。不要把示例域名当作已经发布的服务。
4. 打开该服务的 `/terminal/`，或在 GitHub Pages 工作台连接设置中填写服务地址。
5. Blueprint 默认开放有限额的访客体验，无需访客输入令牌。DeepSeek Key 始终不进入浏览器。如果只想自己使用，在服务环境变量中设置 `FINAGENT_API_TOKEN`，将 `FINAGENT_PUBLIC_LIVE` 设为 `false`，然后在工作台填写这个服务令牌。
6. 执行下面的验证命令。首次打开休眠服务时先等待唤醒，再提交研究任务。

Render Free 会在闲置约 15 分钟后休眠，重新访问需要冷启动；本地文件在重启、重新部署或休眠后可能丢失，因此任务历史、缓存、SQLite 配额计数不具有跨重启持久性。这适合面试演示，不能据此声称持续生产可用性。[Render 免费服务限制](https://render.com/docs/free)

## 公开免令牌体验

一键公开演示必须同时满足：有独立后端、服务端已配置模型/数据访问、前端连接到该后端、真实任务与导出均通过验证。

当前 Blueprint 已将 `FINAGENT_API_TOKEN` 留空并设 `FINAGENT_PUBLIC_LIVE=true`。独立预算服务的默认全局限额为 12 次发行人分析/过去 24 小时、3 次/过去一小时，同时只执行一项研究、等待队列最多一项；这是次数限制，不是人民币账单上限。SQLite 仍会检查单来源限额；反向代理后可能共享同一来源。

Blueprint 将预算放在同区域的独立 Key Value 服务，Web 休眠、重启后仍可使用原计数。它仅接受 Render 私网访问，内存满时不逐出预算键。**免费 Key Value 本身重启仍可能丢失数据，因此不能据此保证不可重置的长期金额预算。** 更严格的预算需要自己的 VPS + 持久 Docker 卷，或部署者另行选择支持持久存储的方案。仓库不会自动升级实例。[Key Value 存储说明](https://render.com/docs/key-value)

## 已有服务器：Docker + HTTPS 反向代理

已有 `compose.yaml` 将容器监听绑定到 `127.0.0.1:8090`，并使用 `workbench-data` 命名卷保存 SQLite 与缓存。将配置填入服务器的 `.env` 后运行：

```bash
docker compose up --build -d
```

在已有 HTTPS 反向代理中将自己的 API 域名转发到 `127.0.0.1:8090`。保持单个 Uvicorn worker；多个进程共同使用当前队列会导致调度冲突。允许 GitHub Pages 连接时设置：

```dotenv
FINAGENT_ALLOWED_ORIGINS=https://qiqiyzhu.github.io
FINAGENT_ENABLE_LIVE=true
FINAGENT_LIVE_REQUESTS_PER_HOUR=3
FINAGENT_LIVE_GLOBAL_PER_DAY=12
```

跨域来源只写协议和主机，不加 `/FinSearchComp-Audit` 路径。服务访问令牌与模型密钥通过服务器私有配置管理；不要提交 `.env`、把密钥放到 URL 或写入构站产物。

## 部署验收

只检查健康、前端资源、配置、CORS 和新 API 路由，**默认不调用付费模型**：

```bash
python scripts/verify_live_deployment.py --base-url https://YOUR-ASSIGNED-SERVICE.onrender.com
```

实际提交一项研究、等待完成并核验事件和 Markdown / JSON 导出：

```bash
# 如服务有访问令牌，先在当前进程的 FINAGENT_API_TOKEN 环境变量中配置。
python scripts/verify_live_deployment.py --base-url https://YOUR-ASSIGNED-SERVICE.onrender.com --submit
```

`--submit` 会消耗配置的真实模型用量；回执默认写到 `build/verification/live-deployment.json`，含服务版本、任务状态、检查结果和报告 SHA-256，不包含任何密钥。健康检查通过只能证明服务可达，真实任务通过才证明联网研究路径可用。

最后从 GitHub Pages 发起一个新问题，确认研究步骤、材料读取时间、引用链接、报告导出均显示；同时确认冷启动失败会显示真实错误而不会换成离线数据冒充实时。

## 托管选择记录

2026-09-26 检查的本机 OpsPilot / RepoPilot 项目已有 DeepSeek 配置与本地容器方案，但未发现已授权的公网应用托管凭据，因此不能据此推定已有线上服务。本部署文件是可复用交付，实际服务地址与上线回执必须在账号接入后产生。

Hugging Face 当前文档注明：新建运行计算的 Gradio / Docker Space 要求付费计划，即便 CPU Basic 的小时价格为零。因此这里没有把它列成无需条件的免费替代方案。[Hugging Face Spaces 资源说明](https://huggingface.co/docs/hub/spaces-overview)

Blueprint 字段、Secret 与部署按钮采用官方配置方式。[Render Blueprint](https://render.com/docs/blueprint-spec) · [部署按钮](https://render.com/docs/deploy-to-render)
