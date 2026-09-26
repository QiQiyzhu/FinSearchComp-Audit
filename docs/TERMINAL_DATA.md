# 可回放的年度财务数据集

数据集服务于 v3 工作台的公司研究、跨公司并列比较、历史时点查询和结构化问答。它覆盖 8 家公司的 25 个年度财务指标、58 个申报可用事件、171 个年度版本及 1,787 条可逐行核验的证据。财年范围为 FY2019–FY2026，具体终点随公司和申报可用情况而异。

这里的数字是数据规模，不是模型准确率。模型成绩另见独立评测记录。

## 来源与可复现性

- 原始来源为 SEC 官方 CompanyFacts API，采集于 2026-09-22。8 份完整载荷保存于 [`research_workbench/data/terminal_upstream/`](../research_workbench/data/terminal_upstream/)，共约 2.13 MB gzip。
- gzip 解压内容为按键排序的 UTF-8 JSON，压缩使用 `mtime=0`。这些是完整载荷的确定性序列化，不冒充原始 HTTP 响应字节。
- `manifest.json` 分开保存 `canonical_payload_sha256`、最初下载时的 `upstream_response_sha256`、压缩文件 SHA-256、来源 URL 和逐公司抓取时间。
- 公开 [`finance_cube.json`](../site/workbench/data/finance_cube.json) 约 5.26 MB，gzip 后约 0.31 MB。所有值由归档重建，不需要 API 密钥或付费模型。

```bash
python -m research_workbench.generate_terminal --output build/finance_cube.json
python -m unittest research_workbench.test_terminal_data -v
```

常规重建只使用仓库中的 gzip。`--freeze-cache` 是显式的重新采集归档步骤，只读既有下载缓存；重新冻结数据必须作为新版本披露，不能替换已经用于首评的数据。

## “在当时可得”具体指什么

SEC CompanyFacts 提供 `filed` 日期，没有精确到盘中的申报接受时间。本数据集采取保守日粒度规则：**申报后下一日**起可用。例如 `filed=2024-07-30` 的事实从 `2024-07-31` 起纳入。同一天查询会排除该事实。

每个公司的 `events` 按 `available_from` 排序。查询选择最后一个 `available_from <= cutoff` 的事件，之后只能使用这个事件列出的 `annual_ids`。一个旧财年的数据在后续申报中发生变化，会形成新的年度版本；旧事件继续指向旧版本。不得先拿最新年度数值，再只改变页面上显示的日期。

这仍然是**从较晚抓取的 CompanyFacts 历史条目重建**，不是在历史时刻保存的完整数据库。它不能证明某个数字没有提前通过业绩公告、新闻或其他渠道公开，也不衡量盘中交易可用性。超过该次抓取日的查询返回 `unknown`，避免声称已完整覆盖此后的申报。

## 期间、口径与计算约束

1. 只纳入 `10-K` / `10-K/A`、USD、有效有限数值；持续期间须为 330–380 天。季度、8-K、每股口径、交易价格不在此数据集内。
2. 财年由申报 accession 的最新年度端点及 `fy` / `fp=FY` 锚定，再对连续年度端点回推。SEC 中比较期条目的 `fy` 可能仍等于当前申报财年，不能直接当成该条事实的财年。
3. 每个精确期间采用最后可用申报。最新同日有不同数值时返回 `conflict`，不会按标签优先级把分歧隐藏掉。
4. 资产、负债、现金和权益采用与该财年结束日一致的 instant 事实。余额查询的 `period_start=null`，其测量时点为 `period_end`。
5. 联合计算的源操作数必须属于同一 accession，期间也必须一致。不能用后来重述的收入与早期未重述利润混算利润率。
6. 金额和比率以有理数精确计算；只在最终展示时保留两位小数。百分点变化从未取整的比率计算，不对页面上已取整的比率直接相减。
7. 分母为零或负数时，不输出具有误导性的增长百分比或正分母比率。金额差仍可以独立计算。
8. 毛利没有直接 `GrossProfit` 事实时，允许以同份申报、同期间的收入减营业成本计算，并明确标记 `operation=subtract` 和公式。
9. 不以“资产减归母权益”代替总负债，因为还可能存在非控股权益等口径差异。缺少 `Liabilities` 时明确缺项。
10. 资本支出仅采用纯 PP&E 现金支付，不把 PP&E 与无形资产的合并支付、租赁增加额等替代进去。所有缺项都不是零。

## 指标与覆盖

报告事实包括营业收入、营业利润、净利润、经营现金流、纯 PP&E 资本支出、研发费用、毛利、营业成本、总资产、总负债、归母股东权益、现金及现金等价物、流动资产和流动负债。

派生指标包括自由现金流、营业利润率、净利率、毛利率、研发费用率、净利润现金转化率、资产负债率、流动比率、现金资产比、资本支出收入比和自由现金流率。百分比使用 `%`，流动比率使用 `x`，同比金额差使用 USD，利润率变化使用 `percentage_points`。

以截止日 2025-04-01、FY2024 为例，200 个公司×指标单元中有 186 个可回答、14 个明确缺项。这是**覆盖率**，不是正确率：

| 公司 | 可回答 / 25 | 主要缺项原因 |
|---|---:|---|
| Apple、Alphabet、Meta、Microsoft、Tesla | 各 25/25 | 在本目录内均有操作数 |
| AMD | 23/25 | 无直接总负债及其派生比率 |
| NVIDIA | 21/25 | 缺纯 PP&E 资本支出口径及相关派生量 |
| Amazon | 17/25 | 缺本目录定义的 PP&E、研发、直接总负债及相关派生量 |

以上不会外推为全市场覆盖。不同财年、截止日和指标的覆盖率不同。

## 前端数据契约

```text
cube
  schema_version / dataset_id / captured_at / policy / statistics
  metric_catalog[metric_id]
  companies[ticker]
    source / fiscal_years
    events[{ available_from, filed, accessions, annual_ids }]
    annuals[annual_id]
      fiscal_year / period_start / period_end
      metrics[metric_id]
      changes[metric_id]          # 相对前一连续财年的同比 % 或百分点变化
      amount_changes[metric_id]   # USD 指标相对前一财年的金额差
  evidence[evidence_id]
```

一个指标对象包含 `status`、`value`、`exact_value`、`display_value`、`unit`、完整 `evidence_ids`、`formula`、`operation`、`inputs` 和 `reason`。可用状态为 `available`；其余状态分别为：

- `unavailable_as_of`：已知所需事实只存在于截止日之后的申报，或该年度在截止日尚不可用。
- `unknown`：归档中缺少该口径、操作数无法保持同一申报版本，或查询日期超过抓取日。
- `unsupported`：指标/年度在范围之外，或比率数学定义不满足本协议。
- `conflict`：最新同日数值冲突或计算期间冲突。

缺失对象没有数值。对于缺失指标，已知的部分操作数引用只是诊断材料，不能把这些引用解释成完整答案证据。

`evidence` 保存原始 `raw_row`，包括 accession、filed、fy/fp、frame（如存在）、start/end 和 val；同时保存来源载荷与单条事实的指纹。Python 接口为：

```python
cube = load_cube()
state = select_state(cube, "MSFT", "2024-07-31")
value = query_metric(cube, "MSFT", "2024-07-31", 2024, "operating_margin")
gate = evidence_gate(cube, value["evidence_ids"], "2024-07-31", ticker="MSFT")
```

证据门只检查引用存在、公司一致及时间可用性；它本身不证明回答的全部主张正确。答案还需要匹配受支持指标、操作数、期间和数值。工作台综合模型只允许选择已验证结论，不允许填补缺失数值。

## 验证范围

21 个数据层测试包含人工合成反例和全数据检查：未来申报、后续重述、相同日期的标签冲突、非年度数据排除、跨版本计算拒绝、负分母、精确百分点、余额时点、证据门拒绝，以及 1,787 条证据逐行匹配归档、所有事件无未来引用、全部展示值复算和重建字节一致性。其中 2 项明确标记为首评暴露问题后的回归检查：现金资产比遗漏与未来财年状态；不冒充首评前测试。

这些是数据与实现一致性检查。独立题集另行冻结、评分和披露首评结果；不能把生成数据再用同一个选择器计算出的通过率称为模型准确率。
