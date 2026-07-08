"""Unit tests for memory retrieval."""

import pytest
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from logicore.memory.types import (
    MemoryHeader, MemoryDomain, MemoryKind, MemoryStability,
    MemoryType, MemoryScore,
)
from logicore.memory.retrieval.retriever import (
    detect_topic,
    compute_decay_score,
    compute_ranking_score,
    should_forget,
    MemoryRetriever,
)


class TestDetectTopic:
    def test_identity_query(self):
        topic = detect_topic("Who am I?")
        assert topic.primary_domain == MemoryDomain.IDENTITY
        assert "who am i" in topic.keywords
    
    def test_preferences_query(self):
        topic = detect_topic("I prefer dark mode")
        assert topic.primary_domain == MemoryDomain.PREFERENCES
        assert "prefer" in topic.keywords
    
    def test_knowledge_query(self):
        topic = detect_topic("What's the project status?")
        assert topic.primary_domain == MemoryDomain.KNOWLEDGE
    
    def test_relationships_query(self):
        topic = detect_topic("Tell me about my team")
        assert topic.primary_domain == MemoryDomain.RELATIONSHIPS
        assert "team" in topic.keywords
    
    def test_question_intent(self):
        topic = detect_topic("What is the deadline?")
        assert topic.intent == "question"
    
    def test_task_intent(self):
        topic = detect_topic("Create a new feature")
        assert topic.intent == "task"
    
    def test_recall_intent(self):
        topic = detect_topic("Remind me of our previous discussion")
        assert topic.intent == "recall"
    
    def test_general_intent(self):
        topic = detect_topic("Hello there")
        assert topic.intent == "general"
    
    def test_multiple_domains(self):
        topic = detect_topic("I prefer my team to use dark mode")
        assert topic.primary_domain in [MemoryDomain.PREFERENCES, MemoryDomain.RELATIONSHIPS]


class TestComputeDecayScore:
    def test_recent_memory_high_decay(self):
        now_ms = datetime.now().timestamp() * 1000
        score = compute_decay_score(now_ms, 0.5, MemoryStability.EVOLVING)
        assert score > 0.8
    
    def test_old_memory_lower_decay(self):
        old_ms = (datetime.now() - timedelta(days=180)).timestamp() * 1000
        score = compute_decay_score(old_ms, 0.5, MemoryStability.EVOLVING)
        assert score < 0.7
    
    def test_stable_memory_decays_slowly(self):
        old_ms = (datetime.now() - timedelta(days=365)).timestamp() * 1000
        # Use high importance so floor is above 0.5
        score = compute_decay_score(old_ms, 0.8, MemoryStability.STABLE)
        assert score > 0.5  # Decays slowly
    
    def test_ephemeral_memory_decays_quickly(self):
        old_ms = (datetime.now() - timedelta(days=3)).timestamp() * 1000
        # Use low importance so floor is below 0.5
        score = compute_decay_score(old_ms, 0.1, MemoryStability.EPHEMERAL)
        assert score < 0.5
    
    def test_importance_boosts_floor(self):
        now_ms = datetime.now().timestamp() * 1000
        score_low = compute_decay_score(now_ms, 0.1, MemoryStability.EVOLVING)
        score_high = compute_decay_score(now_ms, 0.9, MemoryStability.EVOLVING)
        assert score_high >= score_low


class TestComputeRankingScore:
    def test_basic_scoring(self):
        score = compute_ranking_score(decay=0.8, importance=0.7, confidence=0.9)
        expected = 0.8 * 0.6 + 0.7 * 0.25 + 0.9 * 0.15
        assert abs(score - expected) < 0.001
    
    def test_high_decay_matters_most(self):
        score_high_decay = compute_ranking_score(0.9, 0.5, 0.5)
        score_low_decay = compute_ranking_score(0.3, 0.9, 0.9)
        assert score_high_decay > score_low_decay


class TestShouldForget:
    def test_stable_never_forgets(self):
        old_ms = (datetime.now() - timedelta(days=3650)).timestamp() * 1000
        assert should_forget(old_ms, MemoryStability.STABLE) is False
    
    def test_ephemeral_forgets_quickly(self):
        old_ms = (datetime.now() - timedelta(days=5)).timestamp() * 1000
        assert should_forget(old_ms, MemoryStability.EPHEMERAL) is True
    
    def test_recent_not_forgotten(self):
        now_ms = datetime.now().timestamp() * 1000
        assert should_forget(now_ms, MemoryStability.EVOLVING) is False


class TestMemoryRetriever:
    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.scan_memory_files.return_value = []
        store.read_memory_file.return_value = None
        return store
    
    @pytest.fixture
    def retriever(self, mock_store):
        return MemoryRetriever(mock_store, debug=True)
    
    def test_empty_store_returns_empty(self, retriever):
        result = retriever.retrieve("test query")
        assert result == []
    
    def test_session_budget_enforcement(self, retriever):
        retriever._session_bytes = 70000  # Over 60KB limit
        result = retriever.retrieve("test query")
        assert result == []
    
    def test_surfaced_files_not_retrieved_again(self, retriever):
        retriever._surfaced_this_session.add("test.md")
        result = retriever.retrieve("test query")
        # Should not include already surfaced file
        assert len([r for r in result if r[0].filename == "test.md"]) == 0
    
    def test_reset_session_clears_state(self, retriever):
        retriever._surfaced_this_session.add("test.md")
        retriever._session_bytes = 1000
        
        retriever.reset_session()
        
        assert len(retriever._surfaced_this_session) == 0
        assert retriever._session_bytes == 0
    
    def test_format_for_injection_empty(self, retriever):
        result = retriever.format_for_injection([])
        assert result == ""
    
    def test_format_for_injection_with_memories(self, retriever):
        header = MemoryHeader(
            filename="test.md",
            file_path="/path/to/test.md",
            mtime_ms=datetime.now().timestamp() * 1000,
            description="Test memory",
            type=MemoryType.USER,
            domain=MemoryDomain.IDENTITY,
            kind=MemoryKind.FACT,
        )
        memories = [(header, "Test body", 0.85)]
        result = retriever.format_for_injection(memories)
        assert "identity/fact" in result
        assert "relevance 0.85" in result
        assert "Test body" in result
