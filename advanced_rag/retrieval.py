from __future__ import annotations

from collections import Counter
import hashlib
import math
import re
from typing import Iterable

from .models import EvidenceDocument, QuerySpec, RetrievalHit, RoutePlan
from .router import AdaptiveRouter


_LATIN_OR_NUMBER = re.compile(r"[a-z0-9]+(?:[./%-][a-z0-9]+)*", re.IGNORECASE)
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")


def tokenize(text: str) -> list[str]:
    normalized = text.lower()
    tokens = _LATIN_OR_NUMBER.findall(normalized)
    for run in _CJK_RUN.findall(normalized):
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


class BM25Index:
    def __init__(self, documents: Iterable[EvidenceDocument]) -> None:
        self.documents = tuple(documents)
        self.tokens = {
            document.document_id: tokenize(document.text)
            for document in self.documents
        }
        self.term_frequencies = {
            document_id: Counter(items) for document_id, items in self.tokens.items()
        }
        self.document_frequency: Counter[str] = Counter()
        for items in self.tokens.values():
            self.document_frequency.update(set(items))
        self.average_length = (
            sum(len(items) for items in self.tokens.values()) / len(self.documents)
            if self.documents
            else 0.0
        )

    def score(self, query: str, document: EvidenceDocument) -> float:
        query_terms = Counter(tokenize(query))
        if not query_terms or not self.documents:
            return 0.0
        frequencies = self.term_frequencies[document.document_id]
        length = len(self.tokens[document.document_id])
        k1, b = 1.5, 0.75
        score = 0.0
        for term, query_frequency in query_terms.items():
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            df = self.document_frequency[term]
            inverse_document_frequency = math.log(
                1.0 + (len(self.documents) - df + 0.5) / (df + 0.5)
            )
            denominator = frequency + k1 * (
                1 - b + b * length / max(self.average_length, 1.0)
            )
            score += (
                inverse_document_frequency
                * frequency
                * (k1 + 1)
                / denominator
                * min(query_frequency, 2)
            )
        return score


class HashVectorIndex:
    """Deterministic vector baseline with no model download.

    It is not presented as a learned embedding model.  The interface can be
    replaced by a production dense encoder while keeping the rest of ATLAS-RAG
    unchanged.
    """

    def __init__(self, documents: Iterable[EvidenceDocument], dimensions: int = 512):
        self.dimensions = dimensions
        self.vectors = {
            document.document_id: self._vectorize(
                document.text
            )
            for document in documents
        }

    def score(self, query: str, document: EvidenceDocument) -> float:
        return self._cosine(
            self._vectorize(query), self.vectors[document.document_id]
        )

    def _vectorize(self, text: str) -> dict[int, float]:
        counts: Counter[int] = Counter()
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest, "big") % self.dimensions
            counts[bucket] += 1
        norm = math.sqrt(sum(value * value for value in counts.values()))
        if norm == 0:
            return {}
        return {bucket: value / norm for bucket, value in counts.items()}

    @staticmethod
    def _cosine(left: dict[int, float], right: dict[int, float]) -> float:
        if len(left) > len(right):
            left, right = right, left
        return sum(value * right.get(bucket, 0.0) for bucket, value in left.items())


class HybridTemporalRetriever:
    """Fuse sparse, vector, temporal, and source-utility rankings."""

    def __init__(
        self,
        documents: Iterable[EvidenceDocument],
        *,
        router: AdaptiveRouter | None = None,
    ) -> None:
        unique = {document.document_id: document for document in documents}
        self.documents = tuple(unique[key] for key in sorted(unique))
        if not self.documents:
            raise ValueError("at least one evidence document is required")
        self.router = router or AdaptiveRouter()
        self.bm25 = BM25Index(self.documents)
        self.vector = HashVectorIndex(self.documents)

    def retrieve(
        self,
        query: QuerySpec,
        plan: RoutePlan,
        *,
        limit: int,
        mode: str = "temporal",
        corrective: bool = False,
    ) -> list[RetrievalHit]:
        if mode not in {"bm25", "hybrid", "temporal"}:
            raise ValueError("mode must be bm25, hybrid, or temporal")
        limit = max(1, min(limit, len(self.documents)))

        sparse = {
            document.document_id: max(
                self.bm25.score(variant, document)
                for variant in plan.query_variants
            )
            for document in self.documents
        }
        vector = {
            document.document_id: max(
                self.vector.score(variant, document)
                for variant in plan.query_variants
            )
            for document in self.documents
        }
        sparse_normalized = self._normalize(sparse)
        sparse_ranks = self._rank(sparse, query.query_id, "sparse")
        vector_ranks = self._rank(vector, query.query_id, "vector")

        raw: list[tuple[EvidenceDocument, dict[str, float], float]] = []
        for document in self.documents:
            document_id = document.document_id
            rrf = (
                1.0 / (60 + sparse_ranks[document_id])
                + 1.0 / (60 + vector_ranks[document_id])
            )
            semantic = 0.55 * sparse_normalized[document_id] + 0.45 * vector[document_id]
            temporal = self._temporal_compatibility(query, document)
            source_utility = self.router.authority_utility(
                document.source_authority, plan
            )
            components = {
                "bm25": sparse[document_id],
                "bm25_normalized": sparse_normalized[document_id],
                "vector": vector[document_id],
                "rrf": rrf,
                "semantic": semantic,
                "temporal": temporal,
                "source_utility": source_utility,
            }
            raw.append((document, components, rrf))

        rrf_normalized = self._normalize(
            {document.document_id: rrf for document, _, rrf in raw}
        )
        scored: list[tuple[EvidenceDocument, dict[str, float], float]] = []
        for document, components, _ in raw:
            components["rrf_normalized"] = rrf_normalized[document.document_id]
            if mode == "bm25":
                final = components["bm25_normalized"]
            elif mode == "hybrid":
                final = 0.65 * components["rrf_normalized"] + 0.35 * components["semantic"]
            else:
                semantic_weight = 0.62 if not corrective else 0.52
                temporal_weight = 0.25 if not corrective else 0.31
                source_weight = 1.0 - semantic_weight - temporal_weight
                final = (
                    semantic_weight * components["semantic"]
                    + temporal_weight * components["temporal"]
                    + source_weight * components["source_utility"]
                )
            components["final"] = final
            scored.append((document, components, final))

        scored.sort(
            key=lambda item: (
                -item[2],
                self._tie_key(query.query_id, item[0].document_id, mode),
            )
        )
        return [
            RetrievalHit(document=document, rank=index, score=score, component_scores=components)
            for index, (document, components, score) in enumerate(scored[:limit], start=1)
        ]

    @staticmethod
    def _temporal_compatibility(
        query: QuerySpec, document: EvidenceDocument
    ) -> float:
        checks = (
            float(document.is_visible_at(query.cutoff_date)),
            float(document.target_period == query.target_period),
            float(document.revision == query.required_version),
            float(document.unit == query.canonical_unit),
        )
        return sum(checks) / len(checks)

    @staticmethod
    def _normalize(values: dict[str, float]) -> dict[str, float]:
        minimum = min(values.values(), default=0.0)
        maximum = max(values.values(), default=0.0)
        if math.isclose(minimum, maximum):
            return {key: 1.0 if maximum > 0 else 0.0 for key in values}
        return {
            key: (value - minimum) / (maximum - minimum)
            for key, value in values.items()
        }

    @classmethod
    def _rank(
        cls, values: dict[str, float], query_id: str, channel: str
    ) -> dict[str, int]:
        ordered = sorted(
            values,
            key=lambda document_id: (
                -values[document_id],
                cls._tie_key(query_id, document_id, channel),
            ),
        )
        return {document_id: rank for rank, document_id in enumerate(ordered, 1)}

    @staticmethod
    def _tie_key(query_id: str, document_id: str, channel: str) -> str:
        payload = f"{query_id}|{document_id}|{channel}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
