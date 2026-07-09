"""
Unit Tests: Web Tools

Tests for WebSearchTool, UrlFetchTool, and ImageSearchTool (Exa API).
"""

import pytest
from unittest.mock import patch, MagicMock
from logicore.tools.web import (
    WebSearchTool,
    UrlFetchTool,
    ImageSearchTool,
    extract_text_from_html,
    fetch_page_content,
)
from logicore.tools.base import ToolResult


class TestExtractTextFromHtml:
    """Test HTML text extraction helper."""
    
    def test_extract_simple_html(self):
        """Test extracting text from simple HTML."""
        html = "<p>Hello World</p>"
        result = extract_text_from_html(html)
        assert "Hello World" in result
    
    def test_extract_removes_scripts(self):
        """Test that script tags are removed."""
        html = "<p>Content</p><script>alert('hack')</script><p>More</p>"
        result = extract_text_from_html(html)
        assert "alert" not in result
        assert "Content" in result
    
    def test_extract_removes_styles(self):
        """Test that style tags are removed."""
        html = "<p>Content</p><style>.red{color:red}</style><p>More</p>"
        result = extract_text_from_html(html)
        assert ".red" not in result
    
    def test_extract_truncates_long_text(self):
        """Test that long text is truncated."""
        html = "<p>" + "a" * 5000 + "</p>"
        result = extract_text_from_html(html, max_chars=100)
        assert len(result) <= 110  # Allow for "..." suffix
    
    def test_extract_handles_entities(self):
        """Test HTML entity decoding."""
        html = "<p>&amp; &lt; &gt; &quot; &#39;</p>"
        result = extract_text_from_html(html)
        assert "&" in result
        assert "<" in result


class TestWebSearchTool:
    """Test WebSearchTool functionality."""
    
    def test_tool_initialization(self):
        """Test WebSearchTool initializes correctly."""
        tool = WebSearchTool()
        assert tool.name == "web_search"
        assert tool.description
        assert tool.args_schema is not None
    
    def test_tool_schema(self):
        """Test WebSearchTool schema is valid."""
        tool = WebSearchTool()
        schema = tool.schema
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "web_search"
        assert "parameters" in schema["function"]
    
    def test_run_without_api_key(self):
        """Test run returns error without API key."""
        tool = WebSearchTool()
        with patch('logicore.tools.web.get_api_key', return_value=None):
            result = tool.run(user_input="test query")
            assert result["success"] is False
            assert "search failed" in result["error"].lower() or "api key" in result["error"].lower()
    
    def test_run_with_query_alias(self):
        """Test that 'query' parameter works as alias for 'user_input'."""
        tool = WebSearchTool()
        with patch('logicore.tools.web.get_api_key', return_value=None):
            result = tool.run(query="test query")
            assert result["success"] is False  # Will fail due to no API key, but shouldn't crash
    
    def test_run_missing_query(self):
        """Test run returns error without query."""
        tool = WebSearchTool()
        result = tool.run()
        assert result["success"] is False
        assert "required" in result["error"].lower() or "query" in result["error"].lower()
    
    def test_format_results(self):
        """Test results formatting from raw Exa output."""
        tool = WebSearchTool()
        results = [
            {
                "title": "Test Title",
                "url": "https://example.com",
                "author": "Jane Doe",
                "publishedDate": "2026-01-01",
                "text": "Test body content",
                "highlights": ["Test highlight"],
            }
        ]
        formatted = tool._format_results(results)
        assert "Test Title" in formatted
        assert "https://example.com" in formatted
        assert "Test body content" in formatted
        assert "Jane Doe" in formatted
        assert "2026-01-01" in formatted
    
    def test_format_results_empty(self):
        """Test results formatting with empty results."""
        tool = WebSearchTool()
        formatted = tool._format_results([])
        assert "No results" in formatted


class TestUrlFetchTool:
    """Test UrlFetchTool functionality."""
    
    def test_tool_initialization(self):
        """Test UrlFetchTool initializes correctly."""
        tool = UrlFetchTool()
        assert tool.name == "url_fetch"
        assert tool.description
    
    def test_tool_schema(self):
        """Test UrlFetchTool schema is valid."""
        tool = UrlFetchTool()
        schema = tool.schema
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "url_fetch"


class TestImageSearchTool:
    """Test ImageSearchTool functionality."""
    
    def test_tool_initialization(self):
        """Test ImageSearchTool initializes correctly."""
        tool = ImageSearchTool()
        assert tool.name == "image_search"
        assert tool.description
    
    def test_tool_schema(self):
        """Test ImageSearchTool schema is valid."""
        tool = ImageSearchTool()
        schema = tool.schema
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "image_search"
    
    def test_run_without_api_key(self):
        """Test run returns error without API key."""
        tool = ImageSearchTool()
        with patch('logicore.tools.web.get_api_key', return_value=None):
            result = tool.run(query="test query")
            assert result["success"] is False
    
    def test_run_missing_query(self):
        """Test run returns error without query."""
        tool = ImageSearchTool()
        result = tool.run()
        assert result["success"] is False
