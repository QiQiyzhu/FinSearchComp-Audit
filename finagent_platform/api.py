from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from advanced_rag.models import QuerySpec
from advanced_rag.server import build_service

from .platform import FinAgentPlatform
from .store import IdempotencyConflict, RunNotFound, RunStore


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(default="api-request", min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=8_000)
    as_of: date
    target_period: str = Field(min_length=1, max_length=120)
    required_version: str = Field(default="final", min_length=1, max_length=80)
    canonical_unit: str = Field(min_length=1, max_length=80)

    def to_query_spec(self) -> QuerySpec:
        return QuerySpec(
            query_id=self.query_id,
            question=self.question,
            cutoff_date=self.as_of.isoformat(),
            target_period=self.target_period,
            required_version=self.required_version,
            canonical_unit=self.canonical_unit,
        )


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="evaluation", min_length=1, max_length=120)
    queries: list[QueryRequest] = Field(min_length=1, max_length=100)


def create_app(
    platform: FinAgentPlatform,
    *,
    close_platform_on_shutdown: bool = False,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        if close_platform_on_shutdown:
            platform.close()

    app = FastAPI(
        title="FinAgent Audit Platform",
        version="1.0.0",
        description=(
            "Asynchronous, replayable ATLAS-RAG query and evaluation API with "
            "persistent traces and failure analytics."
        ),
        lifespan=lifespan,
    )

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        return platform.health()

    @app.post("/api/v1/query", status_code=status.HTTP_202_ACCEPTED)
    def submit_query(
        body: QueryRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        try:
            return platform.submit_query(
                body.to_query_spec(), idempotency_key=idempotency_key
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/runs/{run_id}")
    @app.get("/api/v1/jobs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        try:
            return platform.get_run(run_id)
        except RunNotFound as error:
            raise HTTPException(status_code=404, detail="run not found") from error

    @app.get("/api/v1/runs/{run_id}/trace")
    def get_trace(run_id: str) -> dict[str, Any]:
        try:
            return platform.get_trace(run_id)
        except RunNotFound as error:
            raise HTTPException(status_code=404, detail="run not found") from error

    @app.post("/api/v1/evaluations", status_code=status.HTTP_202_ACCEPTED)
    def submit_evaluation(
        body: EvaluationRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        try:
            return platform.submit_evaluation(
                [query.to_query_spec() for query in body.queries],
                name=body.name,
                idempotency_key=idempotency_key,
            )
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/evaluations/{run_id}/failures")
    def failure_analytics(run_id: str) -> dict[str, Any]:
        try:
            return platform.failure_analytics(run_id)
        except RunNotFound as error:
            raise HTTPException(status_code=404, detail="run not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post(
        "/api/v1/runs/{run_id}/replay", status_code=status.HTTP_202_ACCEPTED
    )
    def replay(run_id: str) -> dict[str, Any]:
        try:
            return platform.replay(run_id)
        except RunNotFound as error:
            raise HTTPException(status_code=404, detail="run not found") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return app


def create_default_app() -> FastAPI:
    database_path = Path(
        os.environ.get(
            "FINAGENT_DB_PATH",
            Path(__file__).parents[1] / "build" / "finagent-platform.sqlite3",
        )
    )
    platform = FinAgentPlatform(build_service(), RunStore(database_path))
    return create_app(platform, close_platform_on_shutdown=True)
