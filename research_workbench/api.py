from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
import hashlib
import secrets
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import __version__
from .config import ROOT, Settings
from .engine import ResearchEngine
from .workflows import WorkflowEngine, export_report
from .sources import COMPANIES, DEMO_CUTOFF, DEMO_TICKERS, canonical
from .store import AdmissionError, JobService
from .terminal_engine import ApplicationEngine, terminal_cube, terminal_markdown

EXAMPLES = [
    {"id": "msft-cashflow", "title": "Microsoft：AI 投入下的现金流", "question": "分析微软 FY2024 相比 FY2023 的收入、盈利与现金流，资本支出增加后还有多少自由现金流？给出需要继续核验的风险。", "ticker": "MSFT", "as_of": DEMO_CUTOFF, "mode": "demo"},
    {"id": "aapl-quality", "title": "Apple：收入与盈利质量", "question": "分析 Apple FY2024 相比 FY2023 的收入增长与营业利润率，哪些证据支持继续研究，哪些信息仍然缺失？", "ticker": "AAPL", "as_of": DEMO_CUTOFF, "mode": "demo"},
    {"id": "nvda-growth", "title": "NVIDIA：高增长与证据缺口", "question": "复核 NVIDIA FY2024 的收入与经营现金流增长，并检查资本支出口径是否足以计算自由现金流。", "ticker": "NVDA", "as_of": DEMO_CUTOFF, "mode": "demo"},
    {"id": "msft-aapl-comparison", "title": "Microsoft × Apple：财务对比", "question": "并列比较 Microsoft 与 Apple FY2024 的收入增长、营业利润率和自由现金流，标明财年期间差异。", "ticker": "MSFT", "compare_with": "AAPL", "as_of": DEMO_CUTOFF, "mode": "demo"},
]


class BodyLimitMiddleware:
    """Bound actual received bytes, including chunked requests without a length."""
    def __init__(self, app, maximum: int = 16_384):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.maximum:
                await JSONResponse(status_code=413, content={"detail": "请求体超过 16 KB 限制。"})(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=5, max_length=2000)
    ticker: Literal["MSFT", "AAPL", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AMD"]
    as_of: date
    mode: Literal["demo", "snapshot", "live"] = "demo"
    compare_with: Literal["MSFT", "AAPL", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AMD"] | None = None

    @model_validator(mode="after")
    def different_issuers(self):
        if self.compare_with == self.ticker:
            raise ValueError("请选择两个不同的公司进行比较。")
        return self

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        if len(value.strip()) < 5 or any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("问题至少 5 个字符且不能含控制字符。")
        return value.strip()

    @field_validator("as_of")
    @classmethod
    def validate_as_of(cls, value: date) -> date:
        if value < date(2009, 1, 1) or value > date.today():
            raise ValueError("截止日必须在 2009-01-01 至今天之间。")
        return value


class TerminalRequest(ResearchRequest):
    mode: Literal["demo", "snapshot"] = "demo"
    fiscal_year: int = Field(ge=2019, le=2026)
    metric_ids: list[str] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_terminal_scope(self):
        cube = terminal_cube()
        if any(metric not in cube["metric_catalog"] for metric in self.metric_ids):
            raise ValueError("指标不在已支持的年度指标目录中。")
        for ticker in [self.ticker, self.compare_with]:
            if ticker and self.as_of.isoformat() > cube["companies"][ticker]["source"]["retrieved_at"][:10]:
                raise ValueError("截止日不能晚于该公司数据抓取日期。")
        self.metric_ids = list(dict.fromkeys(self.metric_ids))
        return self


def public_config(settings: Settings) -> dict[str, Any]:
    return {"version": __version__, "default_mode": "demo", "default_as_of": DEMO_CUTOFF,
            "modes": {
                "demo": {"available": True, "label": "离线演示", "detail": "真实 SEC 历史快照 + 确定性计算；不调用模型。", "requires_token": False},
                "snapshot": {"available": settings.available("snapshot"), "label": "快照 + DeepSeek", "detail": "历史证据 + 实时模型归纳；数据不是实时行情。", "requires_token": bool(settings.api_token)},
                "live": {"available": settings.available("live"), "label": "实时研究", "detail": "检索 SEC 当前数据库并按截止日过滤，模型按配置启用。", "requires_token": bool(settings.api_token)},
            },
            "features": {"sec": bool(settings.sec_user_agent), "deepseek": bool(settings.deepseek_api_key), "web_search": bool(settings.tavily_api_key), "exports": True, "comparison": True, "question_answers": True, "terminal": True, "persistence": "sqlite"},
            "tickers": [{"ticker": ticker, "name": company["name"]} for ticker, company in COMPANIES.items()], "demo_tickers": DEMO_TICKERS,
            "limits": {"question_chars": 2000, "live_requests_per_hour": settings.live_requests_per_hour, "live_global_per_day": settings.live_global_per_day, "quota_unit": "issuer_analysis", "comparison_units": 2},
            "scope": "单公司及双公司年度 10-K 财务研究；不同期间仅并列展示；不含价格预测或交易执行。"}


def create_app(settings: Settings | None = None, *, engine: ResearchEngine | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    service = JobService(config, engine or ApplicationEngine(config))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service.start()
        yield
        service.close()

    app = FastAPI(title="FinAgent Research Workbench", version=__version__, lifespan=lifespan)
    app.state.service = service
    app.state.settings = config
    app.add_middleware(BodyLimitMiddleware)
    if config.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(config.allowed_origins), allow_credentials=False,
                           allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        if request.method == "POST":
            try:
                if int(request.headers.get("content-length", "0")) > 16_384:
                    return JSONResponse(status_code=413, content={"detail": "请求体过大。"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Content-Length 无效。"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def authorize(mode: str, authorization: str | None) -> None:
        if mode == "demo":
            return
        if not config.available(mode):
            raise HTTPException(503, "此部署尚未启用该模式。请配置服务端凭据和访问策略，或选择离线演示。")
        if config.api_token:
            supplied = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") else ""
            if not secrets.compare_digest(supplied.encode(), config.api_token.encode()):
                raise HTTPException(401, "此模式需要有效的服务访问令牌。", headers={"WWW-Authenticate": "Bearer"})

    def require_job(identifier: str, authorization: str | None) -> dict[str, Any]:
        job = service.store.get(identifier)
        if job is None:
            raise HTTPException(404, "研究任务不存在或已过保留期。")
        # A saved result remains readable when a provider is temporarily disabled,
        # but a configured access token is still enforced.
        if job["request"]["mode"] != "demo" and config.api_token:
            supplied = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") else ""
            if not secrets.compare_digest(supplied.encode(), config.api_token.encode()):
                raise HTTPException(401, "此研究任务需要有效的服务访问令牌。")
        return job

    @app.get("/api/health")
    @app.get("/healthz")
    def health() -> dict[str, Any]:
        with service.store.connection() as db:
            db.execute("SELECT 1").fetchone()
        return {"status": "ok", "version": __version__, "service": "finagent-research-workbench"}

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return public_config(config)

    @app.get("/api/examples")
    def examples() -> dict[str, Any]:
        return {"examples": EXAMPLES}

    @app.post("/api/research", status_code=202)
    def research(body: ResearchRequest, request: Request,
                 authorization: Annotated[str | None, Header()] = None,
                 idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> dict[str, Any]:
        authorize(body.mode, authorization)
        if body.mode != "live" and any(ticker not in DEMO_TICKERS for ticker in [body.ticker, body.compare_with] if ticker):
            raise HTTPException(422, "离线快照当前仅支持 MSFT、AAPL、NVDA。")
        if idempotency_key is not None and not 1 <= len(idempotency_key) <= 128:
            raise HTTPException(422, "Idempotency-Key 必须为 1–128 个字符。")
        # Do not trust arbitrary X-Forwarded-For. Behind a proxy this is a
        # deliberately conservative shared limit; the global budget also applies.
        client = hashlib.sha256((request.client.host if request.client else "unknown").encode()).hexdigest()
        try:
            job = service.submit(body.model_dump(mode="json", exclude_none=True), client, idempotency_key)
        except AdmissionError as exc:
            raise HTTPException(exc.status, exc.detail, headers={"Retry-After": "60"} if exc.status == 429 else None) from exc
        return {"id": job["id"], "status": job["status"]}

    @app.get("/api/research/{identifier}")
    def get_research(identifier: str, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        return require_job(identifier, authorization)

    @app.post("/api/terminal/research", status_code=202)
    def terminal_research(body: TerminalRequest, request: Request,
                          authorization: Annotated[str | None, Header()] = None,
                          idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> dict[str, Any]:
        authorize(body.mode, authorization)
        if idempotency_key is not None and not 1 <= len(idempotency_key) <= 128:
            raise HTTPException(422, "Idempotency-Key 必须为 1–128 个字符。")
        client = hashlib.sha256((request.client.host if request.client else "unknown").encode()).hexdigest()
        payload = body.model_dump(mode="json", exclude_none=True)
        payload["workflow"] = "terminal"
        try:
            job = service.submit(payload, client, idempotency_key)
        except AdmissionError as exc:
            raise HTTPException(exc.status, exc.detail, headers={"Retry-After": "60"} if exc.status == 429 else None) from exc
        return {"id": job["id"], "status": job["status"]}

    @app.get("/api/research/{identifier}/events")
    def events(identifier: str, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        job = require_job(identifier, authorization)
        return {"id": identifier, "status": job["status"], "events": service.store.events(identifier)}

    @app.get("/api/research/{identifier}/export")
    def export(identifier: str, format: Literal["markdown", "json"] = "markdown", authorization: Annotated[str | None, Header()] = None) -> Response:
        job = require_job(identifier, authorization)
        if job["status"] != "completed":
            raise HTTPException(409, "研究尚未完成，无法导出结果。")
        extension = "md" if format == "markdown" else "json"
        renderer = terminal_markdown if job["result"].get("report_type") == "terminal" else export_report
        content = renderer(job["result"]) if format == "markdown" else canonical(job["result"])
        return Response(content, media_type="text/markdown" if format == "markdown" else "application/json", headers={"Content-Disposition": f'attachment; filename="{identifier}.{extension}"'})

    terminal_static = ROOT / "site" / "terminal"
    if terminal_static.is_dir():
        app.mount("/terminal", StaticFiles(directory=str(terminal_static), html=True), name="terminal")
    static = ROOT / "site" / "workbench"
    if static.is_dir():
        app.mount("/workbench", StaticFiles(directory=str(static), html=True), name="workbench-path")
    if static.is_dir():
        app.mount("/", StaticFiles(directory=str(static), html=True), name="workbench")
    return app
