"""
Media Search Tool for Agentry Framework

Provides inline image and video search capabilities similar to Gemini/Perplexity.
Fetches relevant images and YouTube videos to embed inline in responses.
"""

import requests
import os
import re
from typing import Any, Literal, List, Dict, Optional
from pydantic import BaseModel, Field
from .base import BaseTool, ToolResult


class MediaSearchParams(BaseModel):
    query: str = Field(..., description='Search query for finding relevant media (images/videos).')
    media_type: Literal['image', 'video', 'both'] = Field(
        'both',
        description='Type of media to search for: "image" for images, "video" for YouTube videos, "both" for both.'
    )
    num_results: int = Field(
        3,
        description='Number of results to return (1-5). Keep low for inline use.'
    )


class MediaSearchTool(BaseTool):
    """
    Search for images and videos to embed inline in responses.
    
    This tool returns markdown-formatted media that can be directly embedded
    in the response text, creating a rich media experience like Gemini/Perplexity.
    """
    
    name = "media_search"
    description = (
        "Search for images and YouTube videos to embed INLINE in your response. "
        "Use this to enrich explanations with visual content. "
        "Returns markdown-formatted media ready to paste into your response. "
        "Best for: educational topics, how-to guides, explanations of concepts, "
        "product information, and any topic that benefits from visual aids."
    )
    args_schema = MediaSearchParams

    def _search_images(self, query: str, num_results: int = 3) -> List[Dict[str, str]]:
        """
        Search for images using Google Custom Search API with image search enabled.
        Returns image URLs, titles, and source info.
        """
        api_key = os.environ.get("GOOGLE_API_KEY")
        cx = os.environ.get("GOOGLE_CX")
        
        if not api_key or not cx:
            return []
        
        try:
            url = "https://www.googleapis.com/customsearch/v1"
            params = {
                "key": api_key,
                "cx": cx,
                "q": query,
                "searchType": "image",  # Enable image search
                "num": min(num_results, 10),
                "safe": "active",
                "imgSize": "medium",
                "imgType": "photo"
            }
            
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            if "error" in data or "items" not in data:
                return []
            
            results = []
            for item in data["items"][:num_results]:
                results.append({
                    "type": "image",
                    "url": item.get("link", ""),
                    "title": item.get("title", ""),
                    "source": item.get("displayLink", ""),
                    "thumbnail": item.get("image", {}).get("thumbnailLink", item.get("link", "")),
                    "context_url": item.get("image", {}).get("contextLink", "")
                })
            
            return results
            
        except Exception as e:
            print(f"[MediaSearch] Image search error: {e}")
            return []

    def _search_youtube(self, query: str, num_results: int = 2) -> List[Dict[str, str]]:
        """
        Search for YouTube videos using YouTube Data API or Google Custom Search.
        Returns video URLs, titles, thumbnails, and metadata.
        """
        youtube_api_key = os.environ.get("YOUTUBE_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        
        if not youtube_api_key:
            return []
        
        try:
            # Try YouTube Data API first
            yt_url = "https://www.googleapis.com/youtube/v3/search"
            yt_params = {
                "key": youtube_api_key,
                "q": query,
                "part": "snippet",
                "maxResults": min(num_results, 5),
                "type": "video",
                "videoEmbeddable": "true"
            }
            
            response = requests.get(yt_url, params=yt_params, timeout=10)
            data = response.json()
            
            if "items" in data:
                results = []
                for item in data["items"][:num_results]:
                    video_id = item.get("id", {}).get("videoId", "")
                    snippet = item.get("snippet", {})
                    
                    if video_id:
                        results.append({
                            "type": "video",
                            "video_id": video_id,
                            "url": f"https://www.youtube.com/watch?v={video_id}",
                            "title": snippet.get("title", ""),
                            "channel": snippet.get("channelTitle", ""),
                            "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url", 
                                        snippet.get("thumbnails", {}).get("default", {}).get("url", "")),
                            "description": snippet.get("description", "")[:150] + "..."
                        })
                
                return results
            
            # Fallback: Use Google Custom Search to find YouTube videos
            return self._search_youtube_via_google(query, num_results)
            
        except Exception as e:
            print(f"[MediaSearch] YouTube search error: {e}")
            return self._search_youtube_via_google(query, num_results)

    def _search_youtube_via_google(self, query: str, num_results: int = 2) -> List[Dict[str, str]]:
        """Fallback: Search YouTube videos via Google Custom Search."""
        api_key = os.environ.get("GOOGLE_API_KEY")
        cx = os.environ.get("GOOGLE_CX")
        
        if not api_key or not cx:
            return []
        
        try:
            url = "https://www.googleapis.com/customsearch/v1"
            params = {
                "key": api_key,
                "cx": cx,
                "q": f"{query} site:youtube.com",
                "num": min(num_results, 10)
            }
            
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            if "error" in data or "items" not in data:
                return []
            
            results = []
            for item in data["items"][:num_results]:
                link = item.get("link", "")
                # Extract video ID from YouTube URL
                video_id = self._extract_youtube_id(link)
                
                if video_id:
                    results.append({
                        "type": "video",
                        "video_id": video_id,
                        "url": link,
                        "title": item.get("title", ""),
                        "channel": item.get("displayLink", "youtube.com"),
                        "thumbnail": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                        "description": item.get("snippet", "")[:150] + "..."
                    })
            
            return results
            
        except Exception as e:
            print(f"[MediaSearch] Google YouTube fallback error: {e}")
            return []

    def _extract_youtube_id(self, url: str) -> Optional[str]:
        """Extract YouTube video ID from various URL formats."""
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
            r'youtube\.com/v/([a-zA-Z0-9_-]{11})'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _format_as_inline_markdown(self, results: List[Dict[str, str]]) -> str:
        """
        Format search results as inline markdown that renders beautifully.
        Uses special markers that the frontend can detect and render as rich embeds.
        """
        if not results:
            return ""
        
        output_parts = []
        
        for result in results:
            if result["type"] == "image":
                # Standard markdown image with alt text and source
                # Format: ![Alt Text](URL) with source link
                output_parts.append(
                    f'\n\n![{result["title"]}]({result["url"]})\n'
                    f'*Source: [{result["source"]}]({result["context_url"]})*\n'
                )
            
            elif result["type"] == "video":
                # YouTube embed format that frontend will detect and render
                # Using a special format: [![title](thumbnail)](url)
                video_id = result.get("video_id", "")
                if video_id:
                    output_parts.append(
                        f'\n\n<!-- YOUTUBE:{video_id} -->\n'
                        f'[![🎬 {result["title"]}]({result["thumbnail"]})]({result["url"]})\n'
                        f'*📺 {result["channel"]}*\n'
                    )
        
        return "".join(output_parts)

    def run(self, query: str, media_type: str = 'both', num_results: int = 3) -> ToolResult:
        """
        Search for media and return formatted inline content.
        
        Args:
            query: Search query for finding relevant media
            media_type: Type of media ('image', 'video', 'both')
            num_results: Number of results per type (1-5)
            
        Returns:
            ToolResult with markdown-formatted media ready for inline embedding
        """
        if not query:
            return ToolResult(success=False, error="Search query is required")
        
        # Cap results
        num_results = min(max(1, num_results), 5)
        
        all_results = []
        
        try:
            # Search based on media type
            if media_type in ['image', 'both']:
                image_results = self._search_images(query, num_results)
                all_results.extend(image_results)
            
            if media_type in ['video', 'both']:
                video_results = self._search_youtube(query, min(num_results, 2))
                all_results.extend(video_results)
            
            if not all_results:
                return ToolResult(
                    success=True, 
                    content=f"No media found for '{query}'. Continue with text-only explanation."
                )
            
            # Format as inline markdown
            formatted_content = self._format_as_inline_markdown(all_results)
            
            # Include a summary for the agent
            summary = f"✅ Found {len(all_results)} media item(s) for '{query}':\n"
            for r in all_results:
                if r["type"] == "image":
                    summary += f"  - 🖼️ Image: {r['title'][:50]}...\n"
                else:
                    summary += f"  - 🎬 Video: {r['title'][:50]}...\n"
            
            summary += "\n**EMBED THE FOLLOWING INLINE IN YOUR RESPONSE:**\n"
            summary += formatted_content
            
            return ToolResult(success=True, content=summary)
            
        except Exception as e:
            return ToolResult(success=False, error=f"Media search failed: {str(e)}")


# Create singleton instance for easy access
media_search_tool = MediaSearchTool()
