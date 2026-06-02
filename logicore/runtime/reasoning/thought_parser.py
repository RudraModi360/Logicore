"""
ThoughtParser — Structured thought extraction from model responses.

Inspired by gemini-cli's thoughtUtils.ts, provides:
- Extraction of structured reasoning from model text
- Parsing of markdown-delimited thoughts
- Cognitive transparency for complex reasoning
- Integration with reasoning level escalation

Supports various thought formats:
- **subject**: description (gemini-cli style)
- <thinking>...</thinking> (Claude style)
- [Thought]: ... (explicit markers)
- Step 1: ... Step 2: ... (sequential reasoning)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
from enum import Enum, auto


class ThoughtType(Enum):
    """Types of structured thoughts."""
    SUBJECT_DESCRIPTION = auto()  # **subject**: description
    THINKING_BLOCK = auto()       # <thinking>...</thinking>
    EXPLICIT_MARKER = auto()      # [Thought]: ...
    STEP_SEQUENCE = auto()        # Step 1: ...
    CHAIN_OF_THOUGHT = auto()     # Let me think... First...
    UNKNOWN = auto()


@dataclass
class ParsedThought:
    """A single parsed thought unit."""
    thought_type: ThoughtType
    subject: str
    description: str
    raw_text: str
    start_pos: int = 0
    end_pos: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __str__(self) -> str:
        if self.subject:
            return f"{self.subject}: {self.description}"
        return self.description


@dataclass
class ThoughtAnalysis:
    """Complete analysis of thoughts in a response."""
    thoughts: List[ParsedThought] = field(default_factory=list)
    clean_content: str = ""  # Content with thoughts removed
    has_structured_thinking: bool = False
    complexity_score: float = 0.0  # 0-1, higher = more complex reasoning
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def thought_count(self) -> int:
        return len(self.thoughts)
    
    @property
    def subjects(self) -> List[str]:
        """Get all unique thought subjects."""
        return list(set(t.subject for t in self.thoughts if t.subject))


class ThoughtParser:
    """
    Parser for extracting structured thoughts from model responses.
    
    Supports multiple thought formats and provides:
    - Thought extraction with position tracking
    - Complexity scoring for reasoning depth
    - Clean content separation
    
    Usage:
        parser = ThoughtParser()
        
        text = '''
        **Analysis**: The error is in the loop logic.
        **Solution**: We need to add a bounds check.
        
        Here's the fix...
        '''
        
        analysis = parser.parse(text)
        print(analysis.thoughts)  # [ParsedThought(...), ParsedThought(...)]
        print(analysis.complexity_score)  # 0.4
    """
    
    def __init__(
        self,
        extract_thinking_blocks: bool = True,
        extract_subject_descriptions: bool = True,
        extract_step_sequences: bool = True,
        extract_explicit_markers: bool = True,
        min_thought_length: int = 10,
    ):
        """
        Initialize parser with extraction options.
        
        Args:
            extract_thinking_blocks: Extract <thinking>...</thinking> blocks
            extract_subject_descriptions: Extract **subject**: description patterns
            extract_step_sequences: Extract Step N: ... patterns
            extract_explicit_markers: Extract [Thought]: ... markers
            min_thought_length: Minimum length for a valid thought
        """
        self.extract_thinking_blocks = extract_thinking_blocks
        self.extract_subject_descriptions = extract_subject_descriptions
        self.extract_step_sequences = extract_step_sequences
        self.extract_explicit_markers = extract_explicit_markers
        self.min_thought_length = min_thought_length
        
        # Compiled patterns
        self._patterns = self._compile_patterns()
    
    def _compile_patterns(self) -> Dict[ThoughtType, re.Pattern]:
        """Compile regex patterns for thought extraction."""
        patterns = {}
        
        # **subject**: description (gemini-cli style)
        if self.extract_subject_descriptions:
            patterns[ThoughtType.SUBJECT_DESCRIPTION] = re.compile(
                r'\*\*([^*]+)\*\*\s*[:\-–—]\s*(.+?)(?=\n\n|\n\*\*|$)',
                re.DOTALL | re.MULTILINE
            )
        
        # <thinking>...</thinking> (Claude style)
        if self.extract_thinking_blocks:
            patterns[ThoughtType.THINKING_BLOCK] = re.compile(
                r'<thinking>\s*(.*?)\s*</thinking>',
                re.DOTALL | re.IGNORECASE
            )
        
        # [Thought]: ... or [Analysis]: ... etc.
        if self.extract_explicit_markers:
            patterns[ThoughtType.EXPLICIT_MARKER] = re.compile(
                r'\[(?:Thought|Analysis|Reasoning|Consideration|Note)\]\s*[:\-]?\s*(.+?)(?=\n\[|\n\n|$)',
                re.DOTALL | re.IGNORECASE
            )
        
        # Step 1: ..., Step 2: ... etc.
        if self.extract_step_sequences:
            patterns[ThoughtType.STEP_SEQUENCE] = re.compile(
                r'(?:Step\s+)?(\d+)[.):]\s*(.+?)(?=(?:Step\s+)?\d+[.):]\s|\n\n|$)',
                re.DOTALL | re.IGNORECASE
            )
        
        return patterns
    
    def parse(self, text: str) -> ThoughtAnalysis:
        """
        Parse text for structured thoughts.
        
        Args:
            text: Model response text to parse
        
        Returns:
            ThoughtAnalysis with extracted thoughts and metadata
        """
        if not text:
            return ThoughtAnalysis()
        
        thoughts: List[ParsedThought] = []
        positions_to_remove: List[Tuple[int, int]] = []
        
        # Extract thoughts using each pattern
        for thought_type, pattern in self._patterns.items():
            for match in pattern.finditer(text):
                thought = self._create_thought(thought_type, match)
                if thought and len(thought.description) >= self.min_thought_length:
                    thoughts.append(thought)
                    positions_to_remove.append((match.start(), match.end()))
        
        # Also check for chain-of-thought patterns
        cot_thoughts = self._extract_chain_of_thought(text)
        thoughts.extend(cot_thoughts)
        
        # Sort thoughts by position
        thoughts.sort(key=lambda t: t.start_pos)
        
        # Create clean content (remove thought markers)
        clean_content = self._create_clean_content(text, positions_to_remove)
        
        # Calculate complexity score
        complexity_score = self._calculate_complexity(thoughts, text)
        
        return ThoughtAnalysis(
            thoughts=thoughts,
            clean_content=clean_content.strip(),
            has_structured_thinking=len(thoughts) > 0,
            complexity_score=complexity_score,
            metadata={
                "original_length": len(text),
                "clean_length": len(clean_content),
                "thought_types": list(set(t.thought_type.name for t in thoughts)),
            }
        )
    
    def _create_thought(
        self, 
        thought_type: ThoughtType, 
        match: re.Match
    ) -> Optional[ParsedThought]:
        """Create a ParsedThought from a regex match."""
        groups = match.groups()
        
        if thought_type == ThoughtType.SUBJECT_DESCRIPTION:
            subject = groups[0].strip() if groups[0] else ""
            description = groups[1].strip() if groups[1] else ""
        elif thought_type == ThoughtType.THINKING_BLOCK:
            subject = "thinking"
            description = groups[0].strip() if groups[0] else ""
        elif thought_type == ThoughtType.EXPLICIT_MARKER:
            subject = ""
            description = groups[0].strip() if groups[0] else ""
        elif thought_type == ThoughtType.STEP_SEQUENCE:
            subject = f"Step {groups[0]}"
            description = groups[1].strip() if groups[1] else ""
        else:
            return None
        
        return ParsedThought(
            thought_type=thought_type,
            subject=subject,
            description=description,
            raw_text=match.group(0),
            start_pos=match.start(),
            end_pos=match.end(),
        )
    
    def _extract_chain_of_thought(self, text: str) -> List[ParsedThought]:
        """Extract implicit chain-of-thought reasoning."""
        thoughts = []
        
        # Common CoT patterns
        cot_patterns = [
            (r"(?:Let me|I(?:'ll| will)) (?:think|analyze|consider|examine)(?:[^.]*\.)", "planning"),
            (r"First,?\s+([^.]+\.)", "first"),
            (r"(?:Then|Next|After that),?\s+([^.]+\.)", "sequence"),
            (r"(?:Finally|Lastly),?\s+([^.]+\.)", "conclusion"),
            (r"(?:On one hand|However|On the other hand),?\s+([^.]+\.)", "comparison"),
        ]
        
        for pattern, subject in cot_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                content = match.group(1) if match.lastindex else match.group(0)
                if len(content) >= self.min_thought_length:
                    thoughts.append(ParsedThought(
                        thought_type=ThoughtType.CHAIN_OF_THOUGHT,
                        subject=subject,
                        description=content.strip(),
                        raw_text=match.group(0),
                        start_pos=match.start(),
                        end_pos=match.end(),
                    ))
        
        return thoughts
    
    def _create_clean_content(
        self, 
        text: str, 
        positions: List[Tuple[int, int]]
    ) -> str:
        """Create content with thought markers removed."""
        if not positions:
            return text
        
        # Sort positions by start
        positions.sort()
        
        # Build clean content
        result = []
        last_end = 0
        
        for start, end in positions:
            if start > last_end:
                result.append(text[last_end:start])
            last_end = max(last_end, end)
        
        if last_end < len(text):
            result.append(text[last_end:])
        
        return "".join(result)
    
    def _calculate_complexity(
        self, 
        thoughts: List[ParsedThought], 
        text: str
    ) -> float:
        """
        Calculate complexity score for reasoning.
        
        Factors:
        - Number of thoughts
        - Diversity of thought types
        - Length of reasoning
        - Presence of sequential steps
        - Comparison/contrast patterns
        
        Returns:
            Float between 0 and 1
        """
        if not thoughts:
            return 0.0
        
        # Base score from thought count (diminishing returns)
        thought_score = min(len(thoughts) / 10, 0.3)
        
        # Type diversity score
        unique_types = len(set(t.thought_type for t in thoughts))
        diversity_score = min(unique_types / 4, 0.2)
        
        # Sequential reasoning score
        has_steps = any(t.thought_type == ThoughtType.STEP_SEQUENCE for t in thoughts)
        step_score = 0.15 if has_steps else 0.0
        
        # Thinking block score (explicit deep thinking)
        has_thinking_block = any(t.thought_type == ThoughtType.THINKING_BLOCK for t in thoughts)
        thinking_score = 0.2 if has_thinking_block else 0.0
        
        # Length-based score
        total_thought_length = sum(len(t.description) for t in thoughts)
        length_score = min(total_thought_length / 2000, 0.15)
        
        return min(
            thought_score + diversity_score + step_score + thinking_score + length_score,
            1.0
        )
    
    def extract_subjects(self, text: str) -> List[Tuple[str, str]]:
        """
        Quick extraction of just subject-description pairs.
        
        Returns:
            List of (subject, description) tuples
        """
        analysis = self.parse(text)
        return [(t.subject, t.description) for t in analysis.thoughts if t.subject]
    
    def should_escalate_reasoning(
        self, 
        text: str, 
        complexity_threshold: float = 0.5
    ) -> bool:
        """
        Determine if response complexity suggests reasoning escalation.
        
        Args:
            text: Model response to analyze
            complexity_threshold: Score threshold for escalation
        
        Returns:
            True if reasoning should be escalated
        """
        analysis = self.parse(text)
        return analysis.complexity_score >= complexity_threshold


# Convenience function for simple parsing
def parse_thoughts(text: str) -> ThoughtAnalysis:
    """Parse thoughts using default parser settings."""
    parser = ThoughtParser()
    return parser.parse(text)


def extract_subject_descriptions(text: str) -> List[Tuple[str, str]]:
    """Extract subject-description pairs from text."""
    parser = ThoughtParser()
    return parser.extract_subjects(text)
