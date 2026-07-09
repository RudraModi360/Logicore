import requests
import os
import re
import html as html_mod
import ipaddress
from urllib.parse import urlparse
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult
from logicore.config.settings import get_api_key

# --- SSRF Protection ---

# Internal/private IP ranges that should never be fetched
_BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network('127.0.0.0/8'),      # Loopback
    ipaddress.ip_network('10.0.0.0/8'),       # Private Class A
    ipaddress.ip_network('172.16.0.0/12'),     # Private Class B
    ipaddress.ip_network('192.168.0.0/16'),    # Private Class C
    ipaddress.ip_network('169.254.0.0/16'),    # Link-local (AWS metadata)
    ipaddress.ip_network('::1/128'),            # IPv6 loopback
    ipaddress.ip_network('fc00::/7'),           # IPv6 private
    ipaddress.ip_network('fe80::/10'),          # IPv6 link-local
]

# Blocked URL patterns (cloud metadata endpoints, internal services)
_BLOCKED_URL_PATTERNS = [
    '169.254.169.254',    # AWS/GCP/Azure metadata
    'metadata.google',    # GCP metadata
    'metadata.azure',     # Azure metadata
    'localhost',          # Local services
    '0.0.0.0',           # Wildcard bind
]

# Allowed URL schemes
_ALLOWED_SCHEMES = {'http', 'https'}


def _validate_url_safety(url: str) -> tuple[bool, str]:
    """Validate URL is safe to fetch (no SSRF). Returns (is_valid, error)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL format"

    # Check scheme
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False, f"URL scheme '{parsed.scheme}' not allowed. Only http/https permitted."

    # Check blocked patterns in hostname
    hostname = parsed.hostname or ''
    for pattern in _BLOCKED_URL_PATTERNS:
        if pattern in hostname:
            return False, f"URL contains blocked hostname pattern: {pattern}"

    # Check if hostname resolves to private/internal IP
    try:
        import socket
        addrinfos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
        for family, _, _, _, sockaddr in addrinfos:
            ip = ipaddress.ip_address(sockaddr[0])
            for network in _BLOCKED_IP_NETWORKS:
                if ip in network:
                    return False, f"URL resolves to internal IP: {ip}"
    except (socket.gaierror, ValueError):
        pass  # DNS resolution failed - still allow the attempt

    return True, ""

# --- Schemas ---

class WebSearchParams(BaseModel):
    user_input: str = Field(..., description='Content to search for.')
    num_results: int = Field(
        5,
        description='Number of search results to return (1-10). Default is 5.'
    )

class UrlFetchParams(BaseModel):
    url: str = Field(..., max_length=2048, description='URL to fetch content from.')

class ImageSearchParams(BaseModel):
    query: str = Field(..., description='Search query for images.')
    num_images: int = Field(
        3, 
        description='Number of images to return (1-10). Default is 3.'
    )

# --- Helpers ---

def extract_text_from_html(html: str, max_chars: int = 3000) -> str:
    """Extract readable text from HTML, removing tags and excess whitespace."""
    # Remove script and style elements
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<header[^>]*>.*?</header>', '', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Remove all remaining HTML tags
    text = re.sub(r'<[^>]+>', ' ', html)
    
    # Decode common HTML entities
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
    text = text.replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'")
    
    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    
    # Truncate to max chars
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
    
    return text


def fetch_page_content(url: str, max_chars: int = 3000) -> Optional[str]:
    """Fetch and extract text content from a URL with SSRF protection."""
    is_valid, err = _validate_url_safety(url)
    if not is_valid:
        return None

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, timeout=10, headers=headers, allow_redirects=False)
        response.raise_for_status()
        
        # Validate redirect targets if any (shouldn't happen with allow_redirects=False)
        return extract_text_from_html(response.text, max_chars)
    except Exception:
        return None


# --- Tools ---

class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the internet using Exa and return the raw search results as-is. "
        "Returns the same result set that a direct Exa web search would produce "
        "(title, URL, full text/highlights, author, and publish date for each result)."
    )
    args_schema = WebSearchParams

    EXA_SEARCH_URL = "https://api.exa.ai/search"

    def _exa_search(self, query: str, num_results: int = 5) -> List[Dict[str, Any]]:
        """Perform an Exa Search and return the raw results as-is."""
        api_key = get_api_key("exa")
        
        if not api_key:
            raise ValueError("EXA_API_KEY environment variable must be set")
        
        payload = {
            "query": query,
            "type": "auto",
            "numResults": min(max(1, num_results), 10),
            "contents": {"text": True, "highlights": True}
        }
        
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            self.EXA_SEARCH_URL,
            json=payload,
            headers=headers,
            timeout=15
        )
        data = response.json()
        
        if "error" in data:
            raise Exception(f"Exa API Error: {data['error'].get('message', 'Unknown error')}")
        
        if "results" not in data:
            return []
        
        # Return raw Exa results unmodified so callers get the same data
        # a direct Exa call would produce.
        return data["results"]

    def _format_results(self, results: List[Dict[str, Any]]) -> str:
        """Format the raw Exa results into readable output without losing data."""
        if not results:
            return "No results found."
        
        lines = ["📊 **Web Search Results**\n"]
        
        for i, item in enumerate(results, 1):
            title = item.get("title", "(untitled)")
            url = item.get("url", "")
            author = item.get("author", "")
            published = item.get("publishedDate", "")
            text = item.get("text", "")
            highlights = item.get("highlights", [])
            
            lines.append(f"{i}. **{title}**")
            lines.append(f"   *Source: {url}*")
            
            meta = []
            if author:
                meta.append(f"Author: {author}")
            if published:
                meta.append(f"Published: {published}")
            if meta:
                lines.append("   " + " | ".join(meta))
            
            content = text or (" ".join(highlights) if highlights else "")
            if content:
                snippet = content[:1500]
                if len(content) > 1500:
                    snippet += "..."
                lines.append(f"   {snippet}")
            
            lines.append("")
        
        return "\n".join(lines)

    def run(self, user_input: str = None, query: str = None, num_results: int = 5, **kwargs) -> ToolResult:
        # Accept 'query' as alias for 'user_input' (models often use 'query')
        if query and not user_input:
            user_input = query
        elif query:
            user_input = query  # Prefer query if both provided
        
        if not user_input:
            return ToolResult(success=False, error="Search query is required")
        
        try:
            results = self._exa_search(user_input, num_results)
            return ToolResult(success=True, content=self._format_results(results))
        except Exception as e:
            return ToolResult(success=False, error="Search failed. Check API key and network connection.")


class UrlFetchTool(BaseTool):
    name = "url_fetch"
    description = "Fetch and extract text content from a URL. Returns clean text, not raw HTML."
    args_schema = UrlFetchParams

    def run(self, url: str) -> ToolResult:
        is_valid, err = _validate_url_safety(url)
        if not is_valid:
            return ToolResult(success=False, error=err)

        content = fetch_page_content(url, max_chars=5000)
        if content:
            return ToolResult(success=True, content=content)
        else:
            return ToolResult(success=False, error="Failed to fetch or parse URL.")


class ImageSearchTool(BaseTool):
    """
    Search for images using Exa Search API.
    Returns images that can be rendered inline in chat messages.
    Similar to Perplexity, ChatGPT, and Gemini Pro's image search capabilities.
    """
    name = "image_search"
    description = (
        "Search for images related to the query. Use this when the user asks about visual topics, "
        "wants to see examples, diagrams, charts, photos, or when showing an image would enhance "
        "the response. Returns image URLs with thumbnails and titles for inline rendering."
    )
    args_schema = ImageSearchParams

    EXA_SEARCH_URL = "https://api.exa.ai/search"

    def _exa_image_search(self, query: str, num_results: int = 3) -> List[Dict[str, str]]:
        """Perform an Exa Search for images."""
        api_key = get_api_key("exa")
        
        if not api_key:
            raise ValueError("EXA_API_KEY environment variable must be set for image search")
        
        payload = {
            "query": query,
            "type": "auto",
            "numResults": min(max(1, num_results), 10),
            "contents": {"highlights": True}
        }
        
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            self.EXA_SEARCH_URL,
            json=payload,
            headers=headers,
            timeout=15
        )
        data = response.json()
        
        if "error" in data:
            raise Exception(f"Exa API Error: {data['error'].get('message', 'Unknown error')}")
        
        if "results" not in data:
            return []
        
        results = []
        for item in data["results"]:
            image_info = {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "thumbnail": item.get("thumbnail", item.get("url", "")),
                "source": item.get("author", "")
            }
            results.append(image_info)
        
        return results

    def run(self, query: str = None, num_images: int = 3, **kwargs) -> ToolResult:
        if not query:
            return ToolResult(success=False, error="Search query is required")
        
        try:
            results = self._exa_image_search(query, num_images)
            
            if not results:
                return ToolResult(
                    success=True, 
                    content=f"No images found for '{query}'."
                )
            
            # Format results as special markdown with image tags
            # The UI will parse these and render inline images
            lines = [f"📷 **Image Search Results for \"{html_mod.escape(query)}\"**\n"]
            lines.append('<div class="inline-images-gallery">')
            
            for i, img in enumerate(results, 1):
                # HTML-escape all values to prevent XSS
                safe_url = html_mod.escape(img["url"], quote=True)
                safe_thumb = html_mod.escape(img["thumbnail"], quote=True)
                safe_title = html_mod.escape(img["title"][:50])
                safe_title_full = html_mod.escape(img["title"])
                safe_source = html_mod.escape(img["source"])

                lines.append(f'<figure class="inline-image-item" data-index="{i}" onclick="window.open(\'{safe_url}\', \'_blank\')">')
                lines.append(f'<img src="{safe_thumb}" data-full-url="{safe_url}" alt="{safe_title_full}" loading="lazy" />')
                lines.append(f'<figcaption>')
                lines.append(f'<span class="image-title">{safe_title}{"..." if len(img["title"]) > 50 else ""}</span>')
                if img["source"]:
                    lines.append(f'<span class="image-source">{safe_source}</span>')
                lines.append(f'</figcaption>')
                lines.append(f'</figure>')
            
            lines.append('</div>')
            
            return ToolResult(success=True, content="\n".join(lines))

        except Exception as e:
            return ToolResult(success=False, error=f"Image search failed: {e}")
