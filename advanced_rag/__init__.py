"""ATLAS-RAG: adaptive, temporal, conflict-aware financial retrieval.

This package is a dependency-light research prototype.  It implements ideas
inspired by recent RAG research without claiming to reproduce any one paper.
"""

from .controller import AtlasRAG
from .models import EvidenceDocument, QuerySpec, RAGDecision
from .retrieval import HybridTemporalRetriever
from .router import AdaptiveRouter

__all__ = [
    "AdaptiveRouter",
    "AtlasRAG",
    "EvidenceDocument",
    "HybridTemporalRetriever",
    "QuerySpec",
    "RAGDecision",
]
