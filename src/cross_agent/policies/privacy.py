"""Privacy policy checks for write and disclosure gates."""

from __future__ import annotations

from typing import Iterable

from cross_agent.config import GuardConfig, WriterConfig
from cross_agent.models import EvidenceItem, MemoryCandidate


FORBIDDEN_PATTERNS = {
    "password",
    "passcode",
    "verification code",
    "private key",
    "secret key",
    "credit card number",
    "social security number",
    "密码",
    "验证码",
    "私钥",
    "银行卡号",
    "身份证号",
}


class PrivacyPolicy:
    def __init__(self, writer_config: WriterConfig, guard_config: GuardConfig):
        self._writer_config = writer_config
        self._guard_config = guard_config

    def can_store(self, candidate: MemoryCandidate) -> tuple[bool, str]:
        haystack = " ".join(
            [
                candidate.subject,
                candidate.predicate,
                candidate.scope,
                str(candidate.value),
            ]
        ).lower()
        denied_patterns = FORBIDDEN_PATTERNS | set(
            self._writer_config.sensitive_denied_patterns
        )
        for pattern in denied_patterns:
            if pattern.lower() in haystack:
                return False, f"denied_sensitive_pattern:{pattern}"
        if candidate.sensitivity == "forbidden":
            return False, "forbidden_sensitivity"
        if candidate.sensitivity in {"high", "sensitive"} and not (
            self._writer_config.allow_sensitive_storage
        ):
            return False, "sensitive_storage_requires_consent"
        return True, "allowed"

    def can_disclose(self, evidence: EvidenceItem, allow_sensitive: bool) -> tuple[bool, str]:
        sensitivity = evidence.memory.sensitivity
        if sensitivity in {"high", "sensitive"} and not (
            allow_sensitive and self._guard_config.allow_sensitive_disclosure
        ):
            return False, "sensitive_disclosure_blocked"
        return True, "allowed"

    def filter_disclosable(
        self, items: Iterable[EvidenceItem], allow_sensitive: bool
    ) -> list[EvidenceItem]:
        return [item for item in items if self.can_disclose(item, allow_sensitive)[0]]
