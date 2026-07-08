"""
Memory subsystem type definitions.

Defines the taxonomy for persistent memory across sessions:
- MemoryDomain: Which aspect of user/context
- MemoryKind: What kind of memory
- MemoryStability: How fast it changes
- MemoryType: Legacy type mapping
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime


class MemoryDomain(str, Enum):
    """Which aspect of user/context the memory pertains to."""

    IDENTITY = "identity"
    PREFERENCES = "preferences"
    RELATIONSHIPS = "relationships"
    KNOWLEDGE = "knowledge"
    DOMAIN = "domain"
    ADDITIONAL_LEARNING = "additional_learning"


class MemoryKind(str, Enum):
    """What kind of memory this is."""

    FACT = "fact"
    GUIDELINE = "guideline"
    CONTEXT = "context"
    POINTER = "pointer"


class MemoryStability(str, Enum):
    """How fast the memory changes."""

    STABLE = "stable"
    EVOLVING = "evolving"
    VOLATILE = "volatile"
    EPHEMERAL = "ephemeral"


class MemoryType(str, Enum):
    """Legacy memory type (maps to domain/kind)."""

    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


# Mapping from legacy type to domain/kind
LEGACY_TYPE_MAPPING = {
    MemoryType.USER: (MemoryDomain.IDENTITY, MemoryKind.FACT),
    MemoryType.FEEDBACK: (MemoryDomain.PREFERENCES, MemoryKind.GUIDELINE),
    MemoryType.PROJECT: (MemoryDomain.KNOWLEDGE, MemoryKind.CONTEXT),
    MemoryType.REFERENCE: (MemoryDomain.KNOWLEDGE, MemoryKind.POINTER),
}


# Half-life in days for decay scoring by stability
STABILITY_HALF_LIFE_DAYS = {
    MemoryStability.STABLE: 365,
    MemoryStability.EVOLVING: 90,
    MemoryStability.VOLATILE: 7,
    MemoryStability.EPHEMERAL: 1,
}


# Max age in days before auto-forget by stability
STABILITY_MAX_AGE_DAYS = {
    MemoryStability.STABLE: float("inf"),
    MemoryStability.EVOLVING: 730,
    MemoryStability.VOLATILE: 30,
    MemoryStability.EPHEMERAL: 3,
}


@dataclass
class MemoryMetadata:
    """Metadata for a memory file parsed from YAML frontmatter."""

    name: str
    description: str
    type: MemoryType
    domain: MemoryDomain = MemoryDomain.KNOWLEDGE
    kind: MemoryKind = MemoryKind.CONTEXT
    confidence: float = 0.7
    importance: float = 0.5
    stability: MemoryStability = MemoryStability.EVOLVING
    created: Optional[datetime] = None
    updated: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    tags: List[str] = field(default_factory=list)
    related_to: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.created is None:
            self.created = datetime.now()
        if self.updated is None:
            self.updated = datetime.now()

        # Infer domain from type if domain wasn't explicitly set
        # (i.e., it's still the default KNOWLEDGE)
        if self.domain == MemoryDomain.KNOWLEDGE and self.type in LEGACY_TYPE_MAPPING:
            inferred_domain, inferred_kind = LEGACY_TYPE_MAPPING[self.type]
            # Only override if the inferred domain is different from default
            if inferred_domain != MemoryDomain.KNOWLEDGE:
                self.domain = inferred_domain
            if self.kind == MemoryKind.CONTEXT and inferred_kind != MemoryKind.CONTEXT:
                self.kind = inferred_kind


@dataclass
class MemoryHeader:
    """Lightweight header for scanning/indexing (no body content)."""

    filename: str
    file_path: str
    mtime_ms: float
    description: str
    type: MemoryType
    domain: Optional[MemoryDomain] = None
    kind: Optional[MemoryKind] = None
    confidence: Optional[float] = None
    importance: Optional[float] = None
    stability: Optional[MemoryStability] = None
    expires_at: Optional[datetime] = None
    tags: List[str] = field(default_factory=list)
    related_to: List[str] = field(default_factory=list)


@dataclass
class TopicDetection:
    """Result of topic detection from user query."""

    primary_domain: MemoryDomain
    secondary_domains: List[MemoryDomain]
    keywords: List[str]
    intent: str  # 'question' | 'task' | 'recall' | 'general'


@dataclass
class MemoryScore:
    """Scoring result for a memory during retrieval."""

    header: MemoryHeader
    decay_score: float
    ranking_score: float
    domain_match: bool = False


# Domain keyword vocabularies for topic detection
DOMAIN_KEYWORDS = {
    MemoryDomain.IDENTITY: [
        "who am i",
        "my role",
        "my background",
        "about me",
        "my skills",
        "my experience",
        "my expertise",
        "my job",
        "my position",
    ],
    MemoryDomain.PREFERENCES: [
        "prefer",
        "like",
        "dislike",
        "style",
        "habit",
        "always",
        "never",
        "i want",
        "i need",
        "don't like",
        "favorite",
        "best",
        "worst",
    ],
    MemoryDomain.RELATIONSHIPS: [
        "team",
        "colleague",
        "family",
        "friend",
        "partner",
        "manager",
        "coworker",
        "report",
        "direct report",
        "boss",
    ],
    MemoryDomain.KNOWLEDGE: [
        "status",
        "goal",
        "deadline",
        "bug",
        "architecture",
        "decision",
        "project",
        "task",
        "issue",
        "feature",
        "requirement",
    ],
    MemoryDomain.DOMAIN: [
        "domain",
        "business",
        "industry",
        "market",
        "customer",
        "client",
        "domain knowledge",
        "domain expertise",
        "domain context",
    ],
    MemoryDomain.ADDITIONAL_LEARNING: [
        "learned",
        "discovered",
        "found out",
        "realized",
        "insight",
        "pattern",
        "observation",
        "trend",
        "anomaly",
    ],
}
