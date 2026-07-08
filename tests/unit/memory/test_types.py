"""Unit tests for memory types."""

import pytest
from datetime import datetime

from logicore.memory.types import (
    MemoryDomain,
    MemoryKind,
    MemoryStability,
    MemoryType,
    MemoryMetadata,
    MemoryHeader,
    TopicDetection,
    MemoryScore,
    LEGACY_TYPE_MAPPING,
    STABILITY_HALF_LIFE_DAYS,
    STABILITY_MAX_AGE_DAYS,
    DOMAIN_KEYWORDS,
)


class TestMemoryDomain:
    def test_all_domains_exist(self):
        assert MemoryDomain.IDENTITY.value == "identity"
        assert MemoryDomain.PREFERENCES.value == "preferences"
        assert MemoryDomain.RELATIONSHIPS.value == "relationships"
        assert MemoryDomain.KNOWLEDGE.value == "knowledge"
        assert MemoryDomain.DOMAIN.value == "domain"
        assert MemoryDomain.ADDITIONAL_LEARNING.value == "additional_learning"
    
    def test_domain_count(self):
        assert len(MemoryDomain) == 6


class TestMemoryKind:
    def test_all_kinds_exist(self):
        assert MemoryKind.FACT.value == "fact"
        assert MemoryKind.GUIDELINE.value == "guideline"
        assert MemoryKind.CONTEXT.value == "context"
        assert MemoryKind.POINTER.value == "pointer"
    
    def test_kind_count(self):
        assert len(MemoryKind) == 4


class TestMemoryStability:
    def test_all_stabilities_exist(self):
        assert MemoryStability.STABLE.value == "stable"
        assert MemoryStability.EVOLVING.value == "evolving"
        assert MemoryStability.VOLATILE.value == "volatile"
        assert MemoryStability.EPHEMERAL.value == "ephemeral"


class TestLegacyTypeMapping:
    def test_user_maps_to_identity_fact(self):
        domain, kind = LEGACY_TYPE_MAPPING[MemoryType.USER]
        assert domain == MemoryDomain.IDENTITY
        assert kind == MemoryKind.FACT
    
    def test_feedback_maps_to_preferences_guideline(self):
        domain, kind = LEGACY_TYPE_MAPPING[MemoryType.FEEDBACK]
        assert domain == MemoryDomain.PREFERENCES
        assert kind == MemoryKind.GUIDELINE
    
    def test_project_maps_to_knowledge_context(self):
        domain, kind = LEGACY_TYPE_MAPPING[MemoryType.PROJECT]
        assert domain == MemoryDomain.KNOWLEDGE
        assert kind == MemoryKind.CONTEXT
    
    def test_reference_maps_to_knowledge_pointer(self):
        domain, kind = LEGACY_TYPE_MAPPING[MemoryType.REFERENCE]
        assert domain == MemoryDomain.KNOWLEDGE
        assert kind == MemoryKind.POINTER


class TestStabilityHalfLife:
    def test_stable_has_longest_half_life(self):
        assert STABILITY_HALF_LIFE_DAYS[MemoryStability.STABLE] == 365
    
    def test_ephemeral_has_shortest_half_life(self):
        assert STABILITY_HALF_LIFE_DAYS[MemoryStability.EPHEMERAL] == 1
    
    def test_half_life_decreases_with_instability(self):
        assert STABILITY_HALF_LIFE_DAYS[MemoryStability.STABLE] > STABILITY_HALF_LIFE_DAYS[MemoryStability.EVOLVING]
        assert STABILITY_HALF_LIFE_DAYS[MemoryStability.EVOLVING] > STABILITY_HALF_LIFE_DAYS[MemoryStability.VOLATILE]
        assert STABILITY_HALF_LIFE_DAYS[MemoryStability.VOLATILE] > STABILITY_HALF_LIFE_DAYS[MemoryStability.EPHEMERAL]


class TestStabilityMaxAge:
    def test_stable_never_expires(self):
        assert STABILITY_MAX_AGE_DAYS[MemoryStability.STABLE] == float('inf')
    
    def test_ephemeral_expires_quickly(self):
        assert STABILITY_MAX_AGE_DAYS[MemoryStability.EPHEMERAL] == 3


class TestDomainKeywords:
    def test_all_domains_have_keywords(self):
        for domain in MemoryDomain:
            assert domain in DOMAIN_KEYWORDS
            assert len(DOMAIN_KEYWORDS[domain]) > 0
    
    def test_identity_keywords(self):
        assert "who am i" in DOMAIN_KEYWORDS[MemoryDomain.IDENTITY]
        assert "my role" in DOMAIN_KEYWORDS[MemoryDomain.IDENTITY]


class TestMemoryMetadata:
    def test_defaults(self):
        meta = MemoryMetadata(
            name="test",
            description="test memory",
            type=MemoryType.USER,
        )
        assert meta.name == "test"
        # Domain is inferred from type (USER -> IDENTITY)
        assert meta.domain == MemoryDomain.IDENTITY
        assert meta.kind == MemoryKind.FACT
        assert meta.confidence == 0.7
        assert meta.importance == 0.5
        assert meta.stability == MemoryStability.EVOLVING
        assert meta.created is not None
        assert meta.updated is not None
        assert meta.tags == []
        assert meta.related_to == []
    
    def test_custom_values(self):
        meta = MemoryMetadata(
            name="custom",
            description="custom memory",
            type=MemoryType.FEEDBACK,
            domain=MemoryDomain.PREFERENCES,
            kind=MemoryKind.GUIDELINE,
            confidence=0.9,
            importance=0.8,
            stability=MemoryStability.STABLE,
            tags=["tag1", "tag2"],
            related_to=["related.md"],
        )
        assert meta.confidence == 0.9
        assert meta.importance == 0.8
        assert meta.stability == MemoryStability.STABLE
        assert meta.tags == ["tag1", "tag2"]


class TestMemoryHeader:
    def test_creation(self):
        header = MemoryHeader(
            filename="test.md",
            file_path="/path/to/test.md",
            mtime_ms=1000000.0,
            description="test header",
            type=MemoryType.USER,
        )
        assert header.filename == "test.md"
        assert header.mtime_ms == 1000000.0
        assert header.tags == []
