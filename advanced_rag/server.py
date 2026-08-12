from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Any
from urllib.parse import urlsplit

from .controller import AtlasRAG
from .dataset import build_corpus, load_cases
from .models import QuerySpec
from .retrieval import HybridTemporalRetriever
from .router import AdaptiveRouter
from .service import AdvancedRAGService


MAX_REQUEST_BYTES = 64 * 1024


def build_service() -> AdvancedRAGService:
    router = AdaptiveRouter()
    retriever = HybridTemporalRetriever(build_corpus(load_cases()), router=router)
    return AdvancedRAGService(AtlasRAG(retriever, router=router))


def make_handler(service: AdvancedRAGService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ATLAS-RAG/1.0"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = urlsplit(self.path).path
            if path == "/healthz":
                self._send_json(HTTPStatus.OK, service.health())
            elif path == "/metrics":
                self._send_json(HTTPStatus.OK, service.metrics())
            else:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "not_found", "path": path},
                )

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = urlsplit(self.path).path
            if path != "/v1/answer":
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "not_found", "path": path},
                )
                return
            try:
                payload = self._read_json()
                query = QuerySpec(
                    query_id=str(payload.get("query_id") or "api-request"),
                    question=str(payload["question"]),
                    cutoff_date=str(payload["cutoff_date"]),
                    target_period=str(payload["target_period"]),
                    required_version=str(payload["required_version"]),
                    canonical_unit=str(payload["canonical_unit"]),
                )
                query.validate()
                response = service.answer(
                    query, request_id=self.headers.get("X-Request-ID")
                )
                self._send_json(HTTPStatus.OK, response)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_request", "detail": str(error)},
                )
            except Exception as error:  # keep stack traces out of responses
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "internal_error", "detail": type(error).__name__},
                )

        def _read_json(self) -> dict[str, Any]:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
            if content_type != "application/json":
                raise ValueError("Content-Type must be application/json")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise ValueError("invalid Content-Length") from error
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError(f"body size must be in 1..{MAX_REQUEST_BYTES} bytes")
            body = self.rfile.read(length)
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            print(
                json.dumps(
                    {
                        "event": "http_access",
                        "client": self.client_address[0],
                        "message": format % args,
                    },
                    ensure_ascii=False,
                )
            )

    return Handler


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Run the ATLAS-RAG JSON service")
    value.add_argument("--host", default="127.0.0.1")
    value.add_argument("--port", type=int, default=8080)
    return value


def main() -> None:
    args = parser().parse_args()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(build_service()))
    print(f"ATLAS-RAG listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
