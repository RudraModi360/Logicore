import requests
import os
import re
from typing import Any, Literal, List, Dict, Optional
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult
from logicore.config.settings import get_api_key

# --- Schemas ---

class WebSearchParams(BaseModel):
    user_input: str = Field(..., description='Content to search for.')
    search_type: Literal['quick', 'detailed', 'deep'] = Field(
        'quick', 
        description='Search depth: "quick" returns snippets only (fast, low context), '
                    '"detailed" fetches top 2 page summaries, "deep" for comprehensive research.'
    )

class UrlFetchParams(BaseModel):
    url: str = Field(..., description='URL to fetch content from.')

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
    """Fetch and extract text content from a URL."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, timeout=10, headers=headers)
        response.raise_for_status()
        
        return extract_text_from_html(response.text, max_chars)
    except:
        return None


# --- Tools ---

class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the internet using Google. "
        "Use 'quick' for fast snippet-based answers (recommended), "
        "'detailed' to fetch top page content, "
        "'deep' for comprehensive LLM-analyzed research."
    )
    args_schema = WebSearchParams

    def _google_search(self, query: str, num_results: int = 5) -> List[Dict[str, str]]:
        """Perform a Google Custom Search."""
        api_key = os.environ.get("GOOGLE_API_KEY")
        cx = os.environ.get("GOOGLE_CX")
        
        if not api_key or not cx:
            raise ValueError("GOOGLE_API_KEY and GOOGLE_CX environment variables must be set")
        
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": api_key,
            "cx": cx,
            "q": query,
            "num": min(num_results, 10)
        }

        response = requests.get(url, params=params, timeout=15)
        data = response.json()

        if "error" in data:
            raise Exception(f"Google API Error: {data['error'].get('message', 'Unknown error')}")

        if "items" not in data:
            return []

        results = []
        for item in data["items"]:
            results.append({
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", "")
            })
        
        return results

    def _is_video_link(self, url: str) -> bool:
        """Check if a URL is a video link."""
        video_domains = [
            'youtube.com', 'youtu.be', 'vimeo.com', 
            'dailymotion.com', 'twitch.tv', 'tiktok.com'
        ]
        return any(domain in url.lower() for domain in video_domains)

    def _format_quick_results(self, results: List[Dict[str, str]]) -> str:
        """Format results using just snippets (low context usage)."""
        if not results:
            return "No results found."
        
        lines = ["📊 **Web Search Results** (Quick Mode)\n"]
        
        for i, r in enumerate(results, 1):
            icon = "🎬" if self._is_video_link(r['link']) else "🔗"
            lines.append(f"{icon} **{i}. {r['title']}**")
            lines.append(f"   {r['snippet']}")
            lines.append(f"   *Source: {r['link']}*\n")
        
        return "\n".join(lines)

    def _format_detailed_results(self, results: List[Dict[str, str]], query: str) -> str:
        """Format results with extracted page content for top 2 results."""
        if not results:
            return "No results found."
        
        lines = ["📊 **Web Search Results** (Detailed Mode)\n"]
        
        # Show all snippets first
        lines.append("### Summary of Results:\n")
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']} - {r['snippet'][:100]}...")
        
        # Fetch detailed content from top 2 non-video results
        lines.append("\n### Detailed Content:\n")
        fetched = 0
        
        for r in results:
            if fetched >= 2:
                break
            if self._is_video_link(r['link']):
                continue
            
            content = fetch_page_content(r['link'], max_chars=2000)
            if content:
                fetched += 1
                lines.append(f"**📄 {r['title']}**")
                lines.append(f"*{r['link']}*\n")
                lines.append(content[:1500])
                lines.append("\n---\n")
        
        return "\n".join(lines)

    def run(self, user_input: str = None, search_type: str = 'quick', query: str = None, **kwargs) -> ToolResult:
        # Accept 'query' as alias for 'user_input' (models often use 'query')
        if query and not user_input:
            user_input = query
        elif query:
            user_input = query  # Prefer query if both provided
        
        if not user_input:
            return ToolResult(success=False, error="Search query is required")
        
        try:
            # Determine result count based on search type
            num_results = 5 if search_type == 'quick' else 8
            results = self._google_search(user_input, num_results)

            if search_type == 'quick':
                # Fast: Just return formatted snippets
                return ToolResult(success=True, content=self._format_quick_results(results))
            
            elif search_type == 'detailed':
                # Medium: Snippets + top 2 page content
                return ToolResult(success=True, content=self._format_detailed_results(results, user_input))
            
            elif search_type == 'deep':
                # Deep: Use LLM to analyze and synthesize
                groq_api_key = get_api_key("groq")
                
                # First get detailed content
                detailed = self._format_detailed_results(results, user_input)
                
                if not groq_api_key:
                    return ToolResult(success=True, content=f"[Deep Search - Analysis Unavailable]\n\n{detailed}")
                
                from groq import Groq
                client = Groq(api_key=groq_api_key)
                
                response = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a research assistant. Analyze the provided search results "
                                "and give a comprehensive, well-structured answer. Include key facts, "
                                "cite sources, and highlight any important information the user should know."
                            )
                        },
                        {
                            "role": "user",
                            "content": f"Query: {user_input}\n\nSearch Results:\n{detailed}"
                        }
                    ],
                    max_tokens=1500
                )
                
                analysis = response.choices[0].message.content
                
                # Add sources at the end
                sources = "\n\n**Sources:**\n"
                for i, r in enumerate(results[:5], 1):
                    sources += f"{i}. [{r['title'][:50]}...]({r['link']})\n"
                
                return ToolResult(success=True, content=analysis + sources)

            return ToolResult(success=True, content=self._format_quick_results(results))

        except Exception as e:
            return ToolResult(success=False, error=f"Search failed: {e}")


class UrlFetchTool(BaseTool):
    name = "url_fetch"
    description = "Fetch and extract text content from a URL. Returns clean text, not raw HTML."
    args_schema = UrlFetchParams

    def run(self, url: str) -> ToolResult:
        content = fetch_page_content(url, max_chars=5000)
        if content:
            return ToolResult(success=True, content=content)
        else:
            return ToolResult(success=False, error=f"Failed to fetch or parse URL: {url}")


class ImageSearchTool(BaseTool):
    """
    Search for images using Google Custom Search API.
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

    def _google_image_search(self, query: str, num_results: int = 3) -> List[Dict[str, str]]:
        """Perform a Google Custom Search for images."""
        api_key = os.environ.get("GOOGLE_API_KEY")
        cx = os.environ.get("GOOGLE_CX")
        
        if not api_key or not cx:
            raise ValueError("GOOGLE_API_KEY and GOOGLE_CX environment variables must be set for image search")
        
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": api_key,
            "cx": cx,
            "q": query,
            "searchType": "image",
            "num": min(max(1, num_results), 10),
            "safe": "active"
        }

        response = requests.get(url, params=params, timeout=15)
        data = response.json()

        if "error" in data:
            raise Exception(f"Google API Error: {data['error'].get('message', 'Unknown error')}")

        if "items" not in data:
            return []

        results = []
        for item in data["items"]:
            image_info = {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "thumbnail": item.get("image", {}).get("thumbnailLink", item.get("link", "")),
                "width": item.get("image", {}).get("width", 0),
                "height": item.get("image", {}).get("height", 0),
                "context_url": item.get("image", {}).get("contextLink", ""),
                "source": item.get("displayLink", "")
            }
            results.append(image_info)
        
        return results

    def run(self, query: str = None, num_images: int = 3, **kwargs) -> ToolResult:
        if not query:
            return ToolResult(success=False, error="Search query is required")
        
        try:
            results = self._google_image_search(query, num_images)
            
            if not results:
                return ToolResult(
                    success=True, 
                    content=f"No images found for '{query}'."
                )
            
            # Format results as special markdown with image tags
            # The UI will parse these and render inline images
            lines = [f"📷 **Image Search Results for \"{query}\"**\n"]
            lines.append('<div class="inline-images-gallery">')
            
            for i, img in enumerate(results, 1):
                # Create a structured format that the UI can parse
                lines.append(f'<figure class="inline-image-item" data-index="{i}" onclick="window.open(\'{img["url"]}\', \'_blank\')">')
                lines.append(f'<img src="{img["thumbnail"]}" data-full-url="{img["url"]}" alt="{img["title"]}" loading="lazy" />')
                lines.append(f'<figcaption>')
                lines.append(f'<span class="image-title">{img["title"][:50]}{"..." if len(img["title"]) > 50 else ""}</span>')
                if img["source"]:
                    lines.append(f'<span class="image-source">{img["source"]}</span>')
                lines.append(f'</figcaption>')
                lines.append(f'</figure>')
            
            lines.append('</div>')
            
            return ToolResult(success=True, content="\n".join(lines))

        except Exception as e:
            return ToolResult(success=False, error=f"Image search failed: {e}")

