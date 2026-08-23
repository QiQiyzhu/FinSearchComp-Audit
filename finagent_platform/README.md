# FinAgent Audit Platform

这一层把已有 ATLAS-RAG 从同步实验函数升级为持久化的软件服务。它复用原有检索、时间审计、证据冲突仲裁和选择性回答，不重新发明算法层。

## 平台能力

```text
POST Query / Evaluation
        │
        ▼
Idempotency Key + Config Hash
        │
        ▼
SQLite Run Store: queued → running → succeeded / failed
        │
        ▼
Bounded Retry Worker → ATLAS-RAG → Failure Classifier
        │
        ├── Run Result
        ├── Versioned Trace
        ├── Failure Analytics
        └── Replay Comparison
```

- `RunStore`：SQLite WAL、唯一 run key、请求冲突检测、显式状态转换；
- `FinAgentPlatform`：后台线程任务、有限次指数退避、批评测子 Run、失败分类和回放；
- `FastAPI`：提交查询、查询 Job、查看 trace、批评测、失败分析和 replay；
- Trace 固定保存 dataset、pipeline、model 三类版本，不只保存最终答案；
- 同一请求默认去重；显式 `Idempotency-Key` 被不同 payload 复用时返回 HTTP 409。

## 启动

```bash
pip install -r requirements-platform.txt
uvicorn finagent_platform.api:create_default_app --factory --host 127.0.0.1 --port 8080
```

数据库默认写入 `build/finagent-platform.sqlite3`，也可以用 `FINAGENT_DB_PATH` 指定。当前规模采用单体 FastAPI + SQLite + 进程内线程池；接口与 Job 状态已解耦，未来需要多进程 worker 时可以替换执行器，不需要改变 API 或 Run schema。

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/query` | 创建异步查询，立即返回 `run_id` |
| `GET` | `/api/v1/runs/{run_id}` | 查询状态、结果、版本和失败标签 |
| `GET` | `/api/v1/runs/{run_id}/trace` | 查看执行重试和 ATLAS 状态轨迹 |
| `POST` | `/api/v1/evaluations` | 创建最多 100 条问题的批评测 |
| `GET` | `/api/v1/evaluations/{run_id}/failures` | 按失败类型聚合并下钻到案例 |
| `POST` | `/api/v1/runs/{run_id}/replay` | 使用保存的请求创建新 Run 并比较 old/new |

## 测试与边界

```bash
python -m unittest finagent_platform.test_platform -v
```

测试覆盖并发幂等、非法状态迁移、超时恢复、最终失败持久化、批评测、失败分类、真实 ATLAS 集成、API schema 与 replay。

当前没有为了规模感引入 Redis、Celery、Kafka 或微服务。进程内任务在进程退出后不能自动恢复；生产化下一步应实现数据库 lease/heartbeat worker，再考虑独立队列。SQLite 适合当前单机 FYP 与数百级 Run，不声称适合大规模多租户服务。

## 已固化的真实故障

首次运行并发与 API 测试时，Windows 在清理临时数据库时报 `WinError 32`。根因不是任务仍在运行，而是 Python 的 SQLite 连接上下文只负责事务提交/回滚，并不会关闭连接；WAL 数据库句柄因此仍被占用。

修复后 `RunStore` 使用显式上下文管理器统一执行 `commit / rollback / close`。并发创建、API 请求和每个测试结束时删除临时数据库共同构成回归保护。这个案例保留了“现象 → 定位 → 根因 → 修复 → 防复发”的完整 Debug 链路。
