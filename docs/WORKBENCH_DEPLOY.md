# FinAgent 工作台：体验与部署

[在线案例](https://qiqiyzhu.github.io/FinSearchComp-Audit/workbench/) · [项目首页](https://qiqiyzhu.github.io/FinSearchComp-Audit/) · [源码](https://github.com/QiQiyzhu/FinSearchComp-Audit)

## 先体验

在线工作台运行在 GitHub Pages，使用带 SEC 原始来源的历史快照。选择案例、查看报告、切换证据与轨迹、导出 Markdown / JSON 都不需要 API Key。页面会标明数据截止日和历史回放状态；不会把任意输入伪装成实时模型回答。

GitHub Pages 只托管静态文件。它不保存模型密钥，也不运行 Python。使用真实模型和实时数据，需要下方的完整服务。工作台可以在连接设置中接入自己部署的服务；推荐直接访问服务提供的同源工作台。

v2 提供公司研究、双公司对比与质量验证。在线静态站点可直接回放四个案例（包括 Microsoft × Apple）；连接完整服务后，`demo` 模式也能对快照覆盖范围内的新问题进行真实计算，不消耗模型额度。`snapshot` / `live` 按已配置的模式运行。

## 一键云端开发环境

[在 GitHub Codespaces 启动](https://codespaces.new/QiQiyzhu/FinSearchComp-Audit?quickstart=1)。环境安装依赖后自动启动 8090 端口；默认保持端口私有。第一次访问即可运行离线案例。

Codespaces 由你的 GitHub 账户提供计算资源，其额度与计费遵循 GitHub 账户设置。若要使用 DeepSeek，在 Codespaces 的私有终端复制 `.env.example` 为 `.env`，按下方启用，然后执行 `bash scripts/codespace-start.sh --restart` 重启本项目启动的服务。不要把 `.env` 提交到 Git。

## 本地 Python

需要 Python 3.11+。在仓库根目录执行：

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell 改用 .venv\Scripts\Activate.ps1
pip install -r requirements-workbench.txt
python -m uvicorn research_workbench.api:create_app --factory --host 127.0.0.1 --port 8090
```

打开 `http://127.0.0.1:8090`。API 交互文档为 `/docs`。Windows 也可直接运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-workbench.ps1
```

## Docker Compose

```bash
docker compose up --build -d
docker compose logs -f workbench
```

打开 `http://127.0.0.1:8090`。容器以非 root 用户运行，数据库与缓存保存在命名卷 `workbench-data`。默认端口只绑定本机。停止服务使用 `docker compose down`；保留命名卷即可保留数据。

## 开启真实 DeepSeek

复制 `.env.example` 为 `.env`，设置以下变量。模型名称可以替换为你的 DeepSeek 账户当前可用型号。

```dotenv
DEEPSEEK_API_KEY=填入自己的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
FINAGENT_ENABLE_LIVE=true
FINAGENT_API_TOKEN=生成一个足够长的随机服务令牌
SEC_USER_AGENT=FinAgentResearch/1.0 your-real-contact@example.com
```

`SEC_USER_AGENT` 请替换为实际项目身份与联系方式，便于 SEC 识别自动访问。设置完成后重启服务。在工作台的连接设置中输入**服务访问令牌**；它不是 DeepSeek Key。模型密钥始终留在服务端。

只在本机使用、服务绑定 `127.0.0.1` 时，可以保留 `FINAGENT_API_TOKEN` 为空，并显式设置 `FINAGENT_PUBLIC_LIVE=true`。这会允许访问该服务的人发起真实模型请求，因此共享部署推荐保留服务令牌。

| 模式 | 数据来源 | 模型行为 | 所需配置 |
|---|---|---|---|
| `demo` | 固定历史 SEC 快照 | 确定性研究报告 | 无 |
| `snapshot` | 固定历史 SEC 快照 | 真实 DeepSeek 证据归纳 | DeepSeek + 启用 live + 服务访问策略 |
| `live` | 运行时获取 SEC Company Facts | 按配置启用 DeepSeek；没有 Key 时明确使用确定性摘要 | 启用 live + 服务访问策略 + SEC 身份；DeepSeek 可选 |

`live` 表示运行时访问财报接口，不是实时证券行情。仍按指定截止日选择已申报的年度事实。`snapshot` 使用快照覆盖的期间，不能代表快照之后的信息。

可选设置 `TAVILY_API_KEY` 增加网页研究上下文。没有该 Key 时，系统明确记录未启用网页搜索；财务计算仍来自 SEC 结构化数据。网页检索不等于全网证据完整，也不能验证所有未来来源。

## 单实例服务配置

| 变量 | 用途 |
|---|---|
| `FINAGENT_DB_PATH` | SQLite 数据库路径；示例为 `build/workbench.sqlite3` |
| `FINAGENT_CACHE_DIR` | SEC 响应缓存目录 |
| `FINAGENT_MAX_WORKERS` / `FINAGENT_MAX_PENDING` | 工作线程与待处理任务容量 |
| `FINAGENT_LIVE_REQUESTS_PER_HOUR` | 单来源 IP 每小时真实发行人分析额度；单公司消耗 1，公司对比消耗 2 |
| `FINAGENT_LIVE_GLOBAL_PER_DAY` | 全局过去 24 小时发行人分析额度；示例为 12 |
| `FINAGENT_HTTP_TRUST_ENV` | 是否继承代理环境；默认 false，TLS 验证仍开启 |
| `FINAGENT_ALLOWED_ORIGINS` | 允许跨域的浏览器来源，逗号分隔；默认不跨域 |

配额按发行人分析次数计算，不是精确金额预算。云主机上部署时，用 HTTPS 反向代理接入，配置服务令牌、持久磁盘与备份；**保持单个 Uvicorn worker**，当前进程内执行器不支持多进程共同调度。Compose 的本机绑定适合放在同机反向代理后面。

从 GitHub Pages 连接另一个 HTTPS 服务时，设置：

```dotenv
FINAGENT_ALLOWED_ORIGINS=https://qiqiyzhu.github.io
```

优先直接访问后端同源页面，避免 HTTPS 页面连接 HTTP 服务的浏览器混合内容限制。不要把模型 Key 放进 Pages、前端脚本、截图或公开报告。

## 验证与网站更新

```bash
python -m unittest discover -s research_workbench -p 'test_*.py' -v
python scripts/smoke_workbench.py --base-url http://127.0.0.1:8090
python scripts/build_product_site.py
```

前两项默认不调用付费模型。构站命令先复现旧研究并校验，再生成 `build/public`：产品首页、工作台与 `research.html` 研究档案。GitHub Actions 发布这一目录；不要用旧的 `reproduce.py --output site` 覆盖新版首页。

当前适合个人研究与面试演示的单实例部署。组织级隔离、RBAC、任务队列集群、备份演练和可用性承诺需要进一步工程工作；这份配置不声称完成这些能力。
