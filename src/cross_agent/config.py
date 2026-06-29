"""Configuration loading for Cross-Agent.

The project intentionally keeps configuration in data files and passes a typed
object through the pipeline. Business modules should not know about local paths,
environment variables, or JSON layout details.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping


@dataclass(frozen=True)
class AppConfig:
    tenant_id: str
    user_id: str
    timezone: str = "UTC"


@dataclass(frozen=True)
class StorageConfig:
    backend: str
    sqlite_path: str


@dataclass(frozen=True)
class WriterConfig:
    min_confidence: float
    default_importance: float
    literalness_allowlist: List[str]
    sensitive_denied_patterns: List[str]
    semantic_extraction_enabled: bool = False
    semantic_extraction_required: bool = False
    session_evidence_mode: str = "useful"
    min_inferred_confidence: float = 0.72
    max_candidate_chars: int = 8000
    max_candidates_per_session: int = 24
    final_candidate_top_k: int = 10
    candidate_conflict_margin: float = 0.08
    candidate_confidence_weight: float = 0.30
    candidate_importance_weight: float = 0.20
    candidate_source_weight: float = 0.15
    candidate_temporal_weight: float = 0.20
    candidate_durability_weight: float = 0.10
    candidate_specificity_weight: float = 0.05
    max_future_skew_days: int = 1
    temporal_half_life_days: Mapping[str, float] = field(
        default_factory=lambda: {
            "fact": 730.0,
            "preference": 180.0,
            "task": 30.0,
            "relation": 365.0,
            "event": 90.0,
            "summary": 365.0,
            "sensitive": 30.0,
        }
    )
    allow_sensitive_storage: bool = False
    redact_rejected_event_payload: bool = True


@dataclass(frozen=True)
class ReaderConfig:
    top_k: int
    feedback_top_n: int
    feedback_terms_per_doc: int
    embedding_weight: float
    token_cosine_weight: float
    lexical_weight: float
    temporal_weight: float
    importance_weight: float
    confidence_weight: float
    structured_type_bonus: float
    staleness_penalty: float
    privacy_penalty: float
    memory_intent_llm_enabled: bool = False
    memory_intent_llm_required: bool = False
    memory_intent_rule_yes_threshold: float = 0.80
    memory_intent_rule_no_threshold: float = 0.20
    memory_intent_llm_min_confidence: float = 0.65
    memory_intent_llm_max_tokens: int = 220
    memory_intent_context_max_chars: int = 3000
    memory_probe_min_score: float = 0.20
    memory_type_match_bonus: float = 0.04
    evidence_verifier_enabled: bool = False
    evidence_verifier_required: bool = False
    evidence_verifier_min_confidence: float = 0.70
    evidence_verifier_max_candidates: int = 20
    evidence_verifier_max_tokens: int = 1200
    domain_synonyms: Mapping[str, List[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class GuardConfig:
    allow_sensitive_disclosure: bool
    require_evidence_for_personalization: bool
    abstain_message: str


@dataclass(frozen=True)
class AnswerConfig:
    max_evidence_items: int
    max_snippet_chars: int


@dataclass(frozen=True)
class LLMConfig:
    enabled: bool
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: int
    temperature: float
    max_tokens: int
    max_retries: int = 4
    retry_base_seconds: float = 2.0


@dataclass(frozen=True)
class EmbeddingConfig:
    enabled: bool
    required: bool
    base_url: str
    model: str
    api_key_env: str
    dimensions: int = 3072
    timeout_seconds: int = 60
    max_retries: int = 2
    max_workers: int = 4
    max_input_chars: int = 24000
    index_on_write: bool = True
    query_prefix: str = "task: search result | query: "
    document_template: str = "title: {title} | text: {text}"


@dataclass(frozen=True)
class EvaluationConfig:
    dataset_path: str
    limit: int
    output_dir: str
    top_k: int


@dataclass(frozen=True)
class Settings:
    app: AppConfig
    storage: StorageConfig
    writer: WriterConfig
    reader: ReaderConfig
    guard: GuardConfig
    answer: AnswerConfig
    llm: LLMConfig
    embedding: EmbeddingConfig
    evaluation: EvaluationConfig


def load_settings(path: str | Path = "configs/default.json") -> Settings:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    with config_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    raw = _apply_env_overrides(raw)
    return Settings(
        app=AppConfig(**raw["app"]),
        storage=StorageConfig(**raw["storage"]),
        writer=WriterConfig(**raw["writer"]),
        reader=ReaderConfig(**raw["reader"]),
        guard=GuardConfig(**raw["guard"]),
        answer=AnswerConfig(**raw["answer"]),
        llm=LLMConfig(**raw.get("llm", _default_llm_config())),
        embedding=EmbeddingConfig(**raw.get("embedding", _default_embedding_config())),
        evaluation=EvaluationConfig(**raw["evaluation"]),
    )


def _apply_env_overrides(raw: Dict[str, Any]) -> Dict[str, Any]:
    overrides = {
        "CROSS_AGENT_DATASET": ("evaluation", "dataset_path"),
        "CROSS_AGENT_SQLITE_PATH": ("storage", "sqlite_path"),
        "CROSS_AGENT_OUTPUT_DIR": ("evaluation", "output_dir"),
        "CROSS_AGENT_TENANT_ID": ("app", "tenant_id"),
        "CROSS_AGENT_USER_ID": ("app", "user_id"),
        "CROSS_AGENT_LLM_MODEL": ("llm", "model"),
        "CROSS_AGENT_LLM_BASE_URL": ("llm", "base_url"),
        "CROSS_AGENT_EMBEDDING_BASE_URL": ("embedding", "base_url"),
        "CROSS_AGENT_EMBEDDING_MODEL": ("embedding", "model"),
    }
    cloned = json.loads(json.dumps(raw))
    for env_name, path in overrides.items():
        value = os.environ.get(env_name)
        if not value:
            continue
        section, key = path
        if section not in cloned:
            cloned[section] = (
                _default_embedding_config()
                if section == "embedding"
                else _default_llm_config()
            )
        cloned[section][key] = value
    return cloned


def _default_llm_config() -> Dict[str, Any]:
    return {
        "enabled": False,
        "base_url": "",
        "model": "",
        "api_key_env": "YUNAI_API_KEY",
        "timeout_seconds": 60,
        "temperature": 0.2,
        "max_tokens": 700,
        "max_retries": 4,
        "retry_base_seconds": 2.0,
    }


def _default_embedding_config() -> Dict[str, Any]:
    return {
        "enabled": False,
        "required": False,
        "base_url": "",
        "model": "gemini-embedding-2-preview",
        "api_key_env": "YUNAI_API_KEY",
        "dimensions": 3072,
        "timeout_seconds": 60,
        "max_retries": 2,
        "max_workers": 4,
        "max_input_chars": 24000,
        "index_on_write": True,
        "query_prefix": "task: search result | query: ",
        "document_template": "title: {title} | text: {text}",
    }
