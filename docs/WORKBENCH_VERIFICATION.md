# FinAgent 工作台验证记录

验证日期：2026-09-22。以下分别记录工程测试、真实数据和模型调用，不把测试通过率包装成金融研究准确率。

## v2 本轮升级验收

| 范围 | 实测结果 | 记录 |
| --- | --- | --- |
| 问题级回答、精确计算、API、比较及额度迁移 | 60 项 Python 测试通过 | `research_workbench/test_*.py` |
| 独立评分器诚信检查 | 8 项通过 | `evals/workbench/test_evaluator.py` |
| 既有研究回归 | 86 项通过 | 原有模块未因产品升级退步 |
| 桌面、390px 手机、免费新问题、对比、来源、质量页、深链接 | 10 项浏览器测试通过 | `e2e/workbench.spec.cjs` |
| 原研究复现与新版网站构建 | 通过 | `python scripts/build_product_site.py` |
| 40 题冻结首评 | 完整满足 20/40 → 38/40；公开失败后回归 40/40 | [质量协议与逐题结果](WORKBENCH_QUALITY.md) |
| 真实浏览器双公司 + DeepSeek | 一次任务、两次模型调用，5,274 tokens | [完整回执](verification/workbench-v2-browser-20260922.json) |
| 真实 SEC 在线获取 + DeepSeek | 一次任务、一次模型调用，2,437 tokens | [完整回执](verification/workbench-v2-live-20260922.json) |
| 八个发行人的财年选择与截止日回归 | 8/8 通过；复用已下载原始数据，无新增网络或模型调用 | [缓存审计](verification/workbench-v2-sec-cache-audit-20260922.json) |

本轮新增真实模型调用共 **3 次、7,711 tokens**；真实工作流检查与 40 题离线质量评测独立。浏览器实际操作包含公司对比、按发行人搜索证据、来源抽屉、Markdown 导出及保存结果深链接恢复。两个发行人的财年期间不一致时不计算差额；比例比较使用未舍入操作数，引用命名空间与原子答案关联一起保留。

[新版工作台截图](assets/workbench/workbench-v2-desktop.png) · [质量页](assets/workbench/workbench-v2-quality.png) · [实际 DeepSeek 操作录像](assets/workbench/workbench-v2-demo.webm)

以下保留 v1 初次交付记录，对应旧版测试条数与当时的实际调用，不能与本轮成绩混算。

## v1 离线工程检查

| 范围 | 实际结果 | 入口 |
|---|---|---|
| 新工作台财务、来源、模型协议、API 与持久任务 | 26 项通过 | `python -m unittest discover -s research_workbench -p 'test_*.py' -v` |
| 原时间审计、ATLAS、检索、游戏 QA 与平台回归 | 86 项通过 | `.github/workflows/finsearch-audit.yml` 列出的原有模块 |
| 浏览器操作与窄屏 | 6 项通过 | `npm test`，场景见 `e2e/workbench.spec.cjs` |
| 完整站点构建与旧研究复现 | 通过 | `python scripts/build_product_site.py` |
| 运行中的 HTTP 服务：创建、读取、导出 | 通过，0 次模型调用 | `python scripts/smoke_workbench.py --base-url http://127.0.0.1:8100` |

新测试检查实际财务不变量：原始响应哈希与行溯源、精确 Decimal 结果、申报前一天拒答、版本冲突、指标标签变化、负值与缺失处理、期间对齐、未知引用、问题范围、摘要对目标指标的覆盖。服务检查包含幂等冲突、容量与额度的事务一致性、服务令牌、重启中断、不伪装实时成功、CORS 与实际请求体大小上限。

浏览器检查包含：首页进入工作台，证据搜索与详情、运行步骤、导出文件、拒绝把新问题映射为固定案例、同源服务真实异步 demo API，报告深链接还原与丢失任务不回退，以及 390px 页面无整体横向溢出。测试不使用付费模型。

## 真实数据核验

三家演示公司的原始 SEC JSON 以压缩文件保存在 [upstream](../research_workbench/data/upstream/)，提取范围和哈希保存在 [demo_companyfacts.json](../research_workbench/data/demo_companyfacts.json)。独立检查将压缩文件还原后重算 SHA-256，逐行核对选取记录，并对照发行人 FY2024 / FY2023 年报的 22 个数值；均一致。原文链接和具体科目在[研究映射文档](WORKBENCH_RESEARCH.md)。

Microsoft、Apple、NVIDIA、Alphabet、Meta、Amazon、Tesla、AMD 的真实 SEC 获取与年度选择结果保存于 [8 家公司覆盖记录](verification/workbench-sec-coverage-20260922.json)。本次统一截止日为 2024-11-01：部分日历财年公司最新可用年度是 2023，系统没有擅自替换成尚未披露的 2024 数据。NVIDIA / Amazon 的缺失科目明确保留。

当前 Company Facts 下载后按 `filed` 日期过滤，不是当年保存的 SEC 数据库镜像；申报日期也不是精确到盘中秒级的可用时间。

## 三次真实 DeepSeek 调用

使用既有服务端配置的 `deepseek-flash`，没有 mock，没有把离线结果标成模型生成。

| 场景 | 实际模型用量 | 实际观察 | 回执 |
|---|---:|---|---|
| 历史快照 + 模型 | 1,335 输入 / 39 输出 | HTTP 任务约 1.172 秒；模型阶段 859ms | [完整回执与报告](verification/workbench-snapshot-20260922.json) |
| 实时 SEC + 模型 | 1,335 输入 / 39 输出 | HTTP 任务约 9.813 秒；模型阶段 1,156ms | [完整回执与报告](verification/workbench-live-20260922.json) |
| 浏览器实际提交 + 模型 | 1,357 输入 / 40 输出 | 模型阶段 1,000ms；证据、轨迹和 Markdown 导出通过，0 个 JS 页面错误 | [浏览器回执](verification/workbench-browser-20260922.json) |

合计 **3 次成功模型调用，4,145 tokens**。两次 HTTP 案例均得到微软 FY2024 自由现金流 `74,071,000,000 USD`，并通过截止日检查。UI 案例从用户按钮提交真实任务，之后只读查看证据与导出。

DeepSeek 实际完成的是**已有已验证事实的选择、排序与后续核验动作选择**；摘要文字由这些事实生成。基本面判断采用显式规则，不声称是独立 LLM 估值、约束强化学习、股票预测或完整 EvidenceLoop 复现。单例耗时不是压测结果，也不是服务等级保证。

首次快照验证的回执已完整写入，但终端中文输出触发 Windows 字符编码错误；修复输出编码后直接核对原回执，没有因此重发付费请求。

## 展示与发布

- [真实浏览器操作录像](assets/workbench/workbench-demo.webm)
- [真实模型报告截图](assets/workbench/workbench-live.png)
- [证据详情](assets/workbench/evidence.png) · [运行轨迹](assets/workbench/trace.png)
- [GitHub 工程与 Docker 检查](https://github.com/QiQiyzhu/FinSearchComp-Audit/actions/workflows/workbench.yml)
- [GitHub Pages 发布流水线](https://github.com/QiQiyzhu/FinSearchComp-Audit/actions/workflows/finsearch-audit.yml)

本机未安装 Docker；容器构建、启动、健康检查和 demo HTTP 验证由 Linux GitHub Actions 执行，其结果以对应提交的 workflow 为准。Codespaces 配置已提供，尚未额外创建计费 Codespace 实例。

本次交付是单实例研究应用与可公开体验的历史案例；不是已验证收益的交易系统，也未进行多租户隔离、压力测试或真实机构上线验收。
