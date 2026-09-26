# FinAgent 3.0 验证记录

验证日期：2026-09-26。首评、曝光后修复、工程检查和真实模型集成分别记录，避免把不同分母合并成“AI 准确率”。

## 财务与意图

| 检查 | 结果 | 记录 |
|---|---:|---|
| 冻结结构化财务题首次评测 | 58/60；保留题 34/36 | [first](verification/terminal-structured-first.json) |
| 同题修复回归 | 60/60；48 个可回答数值、12 个状态拒答 | [regression](verification/terminal-structured-regression.json) |
| 独立意图首评 | 14/16；保留题 6/8 | [first](verification/terminal-nlq-first.json) |
| 意图同题回归 | 16/16 | [regression](verification/terminal-nlq-regression.json) |
| Git archive 首次实现重放 | 76 项逐项结果一致 | [replay](verification/terminal-first-code-replay.json) |
| 网页纯 ESM 与 Python 财务内核 | 60 题输出及独立原始行评分一致 | [parity](verification/browser-kernel-parity.json) |

网页/Python 对照属于曝光后的工程回归，不是第二个盲测。详细题集边界、公司与事件相关性、缺失状态定义见[方法说明](TERMINAL_METHODS.md)。

## 真实 DeepSeek 集成

使用已有服务端配置，共完成 **3 次新终端调用、2,312 tokens**，返回模型名均为 `deepseek-flash`。没有把配置的模型别名当作独立核实的权重版本。

- [两次后端调用](verification/terminal-deepseek-live.json)：MSFT 与 GOOGL 年度研究，8/8 个请求指标通过最终交付检查；异步任务、读取权限、模型记录与 Markdown 导出可用。
- [一次真实网页操作](verification/terminal-browser-deepseek-live.json)：浏览器点击“DeepSeek 整理年度摘要”，3/3 个请求指标通过交付检查，页面无 JavaScript 异常。

模型只能对已核验的事实 ID 进行优先排序；所有已验证的请求事实保留在摘要和答案中。模型不能生成新的财务数字。失败时保留已验证数值并明确记录 fallback。这三次调用验证的是集成可用性，不是模型准确率评测。

## 时间门禁实验

旧 36 次模型回答没有重跑，也未覆盖原始记录。[新确定性门禁回放](PIT_GATE_INTERVENTION.md)只检查当次运行已经取得的证据、截止日、公式与回答，先作接受/拒绝决定，再用旧 scorer 判题。

申报前无支持回答从 13/18 降至 0/18；申报后正确回答覆盖从 18/18 降至 12/18。6 个没有引用但数字正确的记忆回答也被阻止，故不能只报告“合规率上升”而隐去覆盖率损失。

## 工程检查

本地 158 项 Python 检查通过：99 项工作台/数据/门禁/任务测试、10 项新独立评分器变异测试、18 项旧 PIT 检查、8 项旧评分器检查和 23 项原研究回归。覆盖了数值正确但缺少操作数、错公司/期间/单位、未来证据、非法日期、模型不可用、付费请求鉴权、幂等与持久导出。

本地 24 项浏览器检查全部通过，覆盖五个终端工作流、任意受支持财务查询、披露前后切换、证据详情、收藏和导出、加载失败恢复及 390px 移动端。CI 同时运行旧工作台与 PIT 浏览器回归、Docker 构建和真实 HTTP 无付费 smoke。

冻结来源重建、首评分数同步、原码哈希与旧模型原始响应重放均通过。Windows 本地预览明确注册 `.mjs` 的 JavaScript MIME 类型；后端和静态预览使用同一规则。

复现命令见 [README](../README.md)；[GitHub Actions](https://github.com/QiQiyzhu/FinSearchComp-Audit/actions) 保存部署与容器验证日志。此项目仍是有明确数据范围的单实例研究产品，没有宣称具备商业终端的实时数据覆盖、企业权限体系或投资预测能力。
