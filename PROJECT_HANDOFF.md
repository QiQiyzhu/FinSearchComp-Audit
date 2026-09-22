# FinAgent 工作台交接 · 2026-09-22

## 当前运行

- 本机完整工作台：**http://127.0.0.1:8100/**，已接入原有 DeepSeek 配置。
- 公共入口：[项目首页](https://qiqiyzhu.github.io/FinSearchComp-Audit/) / [免 Key 历史体验](https://qiqiyzhu.github.io/FinSearchComp-Audit/workbench/)。
- 本机 8090 被另一款应用占用，因此此次服务使用 8100；没有停止或更改该应用。新安装的默认配置仍为 8090，可自行传入端口。
- 当前应用路径：`C:/Users/yzhu/Documents/FYP/FinSearchComp-product`。服务 PID 在 ignored `build/workbench-server.pid`，v2 日志在 `build/workbench-v2-server.{stdout,stderr}.log`。

手动重启前先确认端口进程属于本项目；在此目录执行：

```powershell
python -m uvicorn research_workbench.api:create_app --factory --host 127.0.0.1 --port 8100
```

也可 `powershell -ExecutionPolicy Bypass -File scripts/start-workbench.ps1 -Port 8100` 建立独立虚拟环境后启动。

## 配置与数据

用户已授权从 RepoPilot / OpsPilot 复用现有 DeepSeek 配置。本项目仅将所需配置写入 ignored `.env`，不输出密钥、不提交密钥。SEC 自动访问身份来自已有 Git 作者联系方式，未写入公开示例。模型默认 `deepseek-flash`。

本机显式启用真实研究，监听回环地址；真实发行人分析额度为每来源每小时 5、全局滚动 24 小时 12。单公司消耗 1，公司对比消耗 2，幂等重读不重复扣额。SQLite 与 SEC 缓存位于 ignored `build/`。公开 Pages 不连接本机、不保存 Key、不消耗模型额度。

未来公共后端部署请配置服务访问令牌、HTTPS 和持久磁盘；完整说明见 [部署指南](docs/WORKBENCH_DEPLOY.md)。当前多进程 worker 不受支持。

## Git 与原工作

新实现位于独立 Git worktree。v1 从 `700291c` 开始并经 PR #8 发布；本轮 v2 分支 `codex/finagent-workbench-v2` 从已合并的 `d8ae038` 开始。首评单公司评分可由 `7f4890d` 重放；后续修复的公开题回归与首评分开。目标仓库为 `QiQiyzhu/FinSearchComp-Audit`，不是上游 `randomtutu/FinSearchComp`。

原目录 `C:/Users/yzhu/Documents/FYP/FinSearchComp` 的未提交论文笔记、EvidenceGapAgent、复现与 README 改动保持原样；本次没有把这些未提交内容混入产品发布。后续合并这些研究改动时，需要正常处理首页 README 的差异，避免直接覆盖产品入口。

## 验证与后续

[验证记录](docs/WORKBENCH_VERIFICATION.md)记录本轮 154 项 Python 检查、10 项浏览器验收、3 次新增真实 DeepSeek 调用，以及八家公司 SEC 选择回归。[质量协议](docs/WORKBENCH_QUALITY.md)保留 40 题首评 95% 与后续公开题回归 40/40，后者不是新的独立成绩。

下一阶段应扩展查询规划、更多指标与市场、完整原文定位、可评测的多轮证据缺口搜索；再依据实际负载引入外部队列、组织权限与系统监控。当前不宣称完成这些能力。
