from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
from threading import RLock
import time
from typing import Any

from .controller import AtlasRAG
from .models import QuerySpec


@dataclass
class _CacheEntry:
    expires_at: float
    value: dict[str, Any]


class TTLCache:
    """Small thread-safe LRU+TTL cache for deterministic answer requests."""

    def __init__(self, max_size: int = 256, ttl_seconds: float = 300.0) -> None:
        if max_size <= 0 or ttl_seconds <= 0:
            raise ValueError("max_size and ttl_seconds must be positive")
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = RLock()

    def get(self, key: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._entries.pop(key, None)
                return None
            self._entries.move_to_end(key)
            return json.loads(json.dumps(entry.value, ensure_ascii=False))

    def set(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._entries[key] = _CacheEntry(
                expires_at=time.monotonic() + self.ttl_seconds,
                value=json.loads(json.dumps(value, ensure_ascii=False)),
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_size:
                self._entries.popitem(last=False)


class AdvancedRAGService:
    def __init__(
        self,
        rag: AtlasRAG,
        *,
        cache: TTLCache | None = None,
    ) -> None:
        self.rag = rag
        self.cache = cache or TTLCache()
        self._stripes = tuple(RLock() for _ in range(64))
        self._metrics_lock = RLock()
        self._metrics = {
            "requests_total": 0,
            "cache_hits_total": 0,
            "abstentions_total": 0,
            "corrective_retrieval_total": 0,
            "errors_total": 0,
            "latency_ms_total": 0.0,
        }

    def answer(self, query: QuerySpec, *, request_id: str | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        cache_key = self._fingerprint(query)
        with self._metrics_lock:
            self._metrics["requests_total"] += 1
        stripe = self._stripes[int(cache_key[:8], 16) % len(self._stripes)]
        with stripe:
            cached = self.cache.get(cache_key)
            if cached is not None:
                latency_ms = (time.perf_counter() - started) * 1000
                with self._metrics_lock:
                    self._metrics["cache_hits_total"] += 1
                    self._metrics["latency_ms_total"] += latency_ms
                cached["cached"] = True
                cached["request_id"] = request_id
                cached["latency_ms"] = round(latency_ms, 3)
                return cached

            try:
                decision = self.rag.answer(query).to_dict()
            except Exception:
                with self._metrics_lock:
                    self._metrics["errors_total"] += 1
                raise

            response = {
                "request_id": request_id,
                "cached": False,
                "decision": decision,
            }
            latency_ms = (time.perf_counter() - started) * 1000
            response["latency_ms"] = round(latency_ms, 3)
            response = json.loads(json.dumps(response, ensure_ascii=False))
            self.cache.set(cache_key, response)
        with self._metrics_lock:
            self._metrics["latency_ms_total"] += latency_ms
            if decision["action"] == "abstain":
                self._metrics["abstentions_total"] += 1
            if decision["used_corrective_retrieval"]:
                self._metrics["corrective_retrieval_total"] += 1
        return response

    def metrics(self) -> dict[str, Any]:
        with self._metrics_lock:
            metrics = dict(self._metrics)
        total = metrics["requests_total"]
        metrics["mean_latency_ms"] = (
            round(metrics["latency_ms_total"] / total, 3) if total else 0.0
        )
        return metrics

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "documents": len(self.rag.retriever.documents),
            "metrics": self.metrics(),
        }

    @staticmethod
    def _fingerprint(query: QuerySpec) -> str:
        payload = json.dumps(
            query.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
