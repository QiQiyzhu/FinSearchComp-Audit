# ATLAS-RAG：面向金融时点问题的高级 RAG 原型

ATLAS-RAG 全称 **Adaptive Temporal Listwise Arbitration and Source-aware RAG**。
它不是把传统 RAG 再包一层，也不声称复现某一篇论文；它把 2024–2026 年高级 RAG 的几条主线组合成
一个可解释、可离线复现、可做消融实验的金融研究原型。

## 1. 为什么需要它？

传统 RAG 常见流程是：问题 → 相似度检索 Top-K 文档 → 把文档全部塞给 LLM → 生成答案。
这个流程有四个问题：

1. 每个问题都用同一个检索器和同一个 K；
2. 只看语义相似，不看证据在截止日是否可用；
3. 多个文档互相冲突时，模型可能随意选择；
4. 证据不足时仍强行回答，无法量化可靠性与覆盖率的权衡。

ATLAS-RAG 把它改成一个带反馈的状态机：

```text
PLAN
  ↓ 根据问题类型选择来源和预算
RETRIEVE
  ↓ BM25 + 确定性向量特征 + 时间/来源效用排序
AUDIT
  ↓ 检查发布日期、目标期间、版本、单位
RESOLVE
  ↓ 构造事实冲突边，做 listwise 证据组仲裁
低置信度？──是──→ CORRECT：扩大检索并提高时间/来源权重
  │ 否
  ↓
ANSWER / ABSTAIN
```

## 2. 论文思想如何映射到代码？

| 论文方向 | ATLAS-RAG 中的工程实现 | 代码 |
|---|---|---|
| Adaptive-RAG / R³AG | 按行情、宏观、财报问题路由来源并分配检索深度 | `router.py` |
| Re³ | 将语义相关性、时间兼容性、来源效用分别打分后融合 | `retrieval.py` |
| Astute RAG / SeCon-RAG | 先过滤字段违规，再对冲突事实做来源感知仲裁 | `controller.py`, `evidence_graph.py` |
| FaithfulRAG / E²RAG | 将候选事实、证据和冲突关系显式化 | `evidence_graph.py` |
| Self-RAG / DRAGIN / GRIP | 低置信度触发第二轮检索，保留状态转换 trace | `controller.py` |
| RAGChecker | 分开报告检索指标与最终决策指标 | `evaluate.py` |

这里只复现了上述论文的**设计思想**，没有使用论文训练权重，也不能把结果写成“复现论文指标”。

## 3. 关键模块

### 自适应路由

路由器根据问题中的可解释信号选择来源：

- 指数、收益、回撤 → point-in-time 行情 API；
- CPI、FOMC、国际收支 → 官方统计或央行；
- 财年、现金流、研发费用 → 监管申报和公司原始财报；
- 未命中结构化来源 → 官方网页与通用 Web Search 回退。

当前规则路由容易审计；以后可以用小模型学习路由，但接口无需改变。

### 混合时间检索

检索器融合四类分数：

```text
semantic = BM25 + HashVector
temporal = 发布日期 + 期间 + 版本 + 单位兼容性
source_utility = 当前问题对来源类型的偏好
final = w1·semantic + w2·temporal + w3·source_utility
```

`HashVector` 是为了让一键复现不下载模型而提供的确定性向量特征基线，不是训练得到的 dense embedding。
生产版可替换为 BGE/E5 等编码器，并保留相同的排序、审计和评测接口。

### 事实级冲突图

系统比较同一问题下的证据，建立五种冲突边：

- `future_version`：一条证据在截止日之后发布；
- `period`：目标期间不同；
- `version`：初值、修订值、最终值不同；
- `unit`：百分比、基点、百万美元等单位不同；
- `value`：其他元数据相同但数值冲突。

通过审计的证据按 `(数值, 单位, 期间, 版本)` 分组，再结合排序分、来源质量和来源多样性做 listwise 仲裁。

### 选择性回答与纠错检索

系统把 Top-1 得分、语义相关性、来源效用、候选间 margin 和冲突惩罚合成置信度。
低于阈值会扩大检索窗口并提高时间/来源权重；第二轮仍不足才拒答。

拒答不是“失败”，而是在高风险领域控制错误成本。评测必须同时报告：

- `coverage`：系统回答了多少题；
- `selective_accuracy`：只看已回答样本，准确率是多少；
- `decision_accuracy`：正确回答和正确拒答的总体比例；
- `corrective_retrieval_rate`：多少问题触发了第二轮检索。

## 4. 防止评测数据泄漏

运行时对象不会包含 `gold_answer`、`supports_gold` 或 `is_perturbed`。此外：

- `xxx-gold`、`xxx-future` 等候选 ID 被替换为不可读的 SHA-256 截断 ID；
- `synthetic://...` 被替换为中性 URL；
- “人工扰动”提示语在建立索引前删除；
- 只有评测脚本能读取隐藏标签并计算指标。

因此算法只能根据问题、证据正文和生产环境本来就应该获取的时间元数据做决策。

## 5. 当前离线结果

数据：20 个真实金融问题，100 个去重候选文档。结果见 [`results/README.md`](results/README.md)。

| 方法 | Recall@5 | MRR@10 | Top-5 未来证据率 |
|---|---:|---:|---:|
| BM25 | 95% | 0.563 | 37% |
| Hybrid RRF | 95% | 0.642 | 42% |
| ATLAS temporal | **100%** | **0.929** | **30%** |

端到端 20 题：决策准确率 95%，回答覆盖率 95%，已回答样本准确率 100%，5% 的问题触发纠错检索。

这些数字是确定性受控实验结果，只能说明模块在当前协议中的行为；不能外推为真实网页或任意 LLM 的总体性能。

## 6. E4–E6：从自适应检索到证据缺口闭环

新增 `agentic.py` 将运行时拆成三个可独立测试的对象：

1. `StructuredGapPlanner`：把自然语言问题编译成明确的证据槽和计算操作；
2. `SufficiencyGate`：检查 final 版本、point-in-time 可见性、单位、缺失与冲突；
3. `AgenticRetriever`：逐个填补尚未满足的槽，并在 Gate 放行后立即停止。

冻结协议包含 8 个合成金融问题，涵盖增长率、绝对变化、比例和利润率变化。E5 为每题构造 complete、
partial、empty、conflict 四种证据条件；E6 扫描 1 / 2 / 3 / 5 / 8 次最大检索预算。

| 指标 | 对照 | 完整系统 |
|---|---:|---:|
| E4 回答准确率 | plain / rewrite 75% | structured planner **100%** |
| E5 正确拒答率 | 33.3% | sufficiency gate **100%** |
| E5 无依据回答率 | 66.7% | sufficiency gate **0%** |
| E6 首次达到 100% | — | 最大预算 5 |
| E6 预算 5 → 8 成本代理 | — | 383.5 → 383.5 |

这里的成本代理是确定性工作量指标，不是 token 账单或延迟；fixture 不代表真实发行人事实。
完整结果见 [`results/agentic/`](results/agentic/)，在线报告见
[`site/agentic-eval/`](../site/agentic-eval/)。

## 7. 运行

```bash
python -m advanced_rag.evaluate
python -m advanced_rag.evaluate_agentic
python -m unittest advanced_rag.test_advanced_rag advanced_rag.test_agentic_eval -v
```

启动标准库实现的线程化 JSON 服务：

```bash
python -m advanced_rag.server --host 127.0.0.1 --port 8080
```

健康检查：

```bash
curl http://127.0.0.1:8080/healthz
```

请求示例：

```bash
curl -X POST http://127.0.0.1:8080/v1/answer \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: demo-001" \
  -d '{
    "query_id": "apple-sales",
    "question": "Apple 2024财年的净销售额是多少？",
    "cutoff_date": "2025-01-01",
    "target_period": "FY2024",
    "required_version": "final",
    "canonical_unit": "USD_million"
  }'
```

接口包含 64 KiB 请求上限、输入校验、TTL+LRU 缓存、热点 key 并发合并、请求 ID、健康检查和运行指标。

## 8. 下一步研究

1. 用真实网页独立解析 JSON-LD、OpenGraph、HTTP Header 和监管申报发布日期；
2. 用 BGE/E5 替换 HashVector，并做 BM25、dense、hybrid、temporal 消融；
3. 加入查询改写和独立检索后端，对 Web/API/filing 路由做真实实验；
4. 在开发集校准拒答阈值并绘制 risk-coverage curve；
5. 扩展到多模型、多次运行和人工双人盲审。
