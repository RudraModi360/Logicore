"""
InputEnricher: Extracted input enrichment logic from Agent.

Handles reference extraction, file/URL resolution, image attachment,
and text extraction from various sources.
"""

import os
import re
import json
import asyncio
import tempfile
from typing import Dict, Any, List, Optional, Union
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)


class InputEnricher:
    """
    Enriches user input by resolving references, attaching images,
    and extracting text from files/URLs.
    
    Extracted from Agent to reduce god class size.
    """
    
    def __init__(self, workspace_root: Optional[str] = None, debug: bool = False):
        self.workspace_root = workspace_root
        self.debug = debug
    
    def _extract_references_from_text(self, text: str) -> List[str]:
        """Extract URL/local path-like references from free-form text."""
        if not isinstance(text, str) or not text.strip():
            return []
        
        image_or_doc_ext = r"(?:png|jpg|jpeg|webp|bmp|gif|tif|tiff|pdf|ppt|pptx|doc|docx|xls|xlsx|csv|txt|md|py|js|ts|json|xml|html|css)"
        patterns = [
            r"https?://[^\s'\"<>]+",
            r"['\"]([A-Za-z]:\\[^'\"\r\n]+)['\"]",
            rf"([A-Za-z]:\\[^\r\n]*?\.{image_or_doc_ext})",
            r"([A-Za-z]:\\[^\s'\"\r\n]+)",
            r"['\"]((?:\.{1,2}[\\/])[^'\"\r\n]+)['\"]",
            rf"((?:\.{1,2}[\\/])[^\r\n]*?\.{image_or_doc_ext})",
            r"((?:\.{1,2}[\\/])[^\s'\"\r\n]+)",
            r"['\"]((?:/[^'\"\r\n]+)+)['\"]",
            rf"((?:/[^\r\n]+)+\.{image_or_doc_ext})",
            r"((?:/[^\s'\"\r\n]+)+)",
        ]
        
        refs: List[str] = []
        seen = set()
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                ref = match.group(1) if match.groups() else match.group(0)
                ref = ref.strip().strip("'\"")
                ref = ref.rstrip(".,;:!?)]}")
                if ref and ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
        return refs
    
    def _resolve_local_reference(self, ref: str) -> Optional[str]:
        """Resolve local file references to absolute paths."""
        if not isinstance(ref, str) or not ref:
            return None
        
        if ref.startswith(("http://", "https://")):
            return None
        
        candidates = []
        if os.path.isabs(ref):
            candidates.append(ref)
        else:
            candidates.append(os.path.abspath(ref))
            if self.workspace_root:
                candidates.append(os.path.abspath(os.path.join(self.workspace_root, ref)))
        
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
        return None
    
    def _is_image_reference(self, ref: str) -> bool:
        """Check if a reference points to an image file."""
        lower = str(ref or "").lower()
        image_exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")
        return lower.endswith(image_exts)
    
    def _extract_text_from_local_file(self, file_path: str) -> Optional[str]:
        """Extract text from a local file via document handlers."""
        try:
            from logicore.document.registry import get_handler
            handler = get_handler(file_path)
            text = handler.get_text()
            return text if isinstance(text, str) and text.strip() else None
        except Exception as e:
            logger.warning(f"Could not extract text from local file {file_path}: {e}")
            return None
    
    def _extract_text_from_url(self, url: str) -> Optional[str]:
        """Fetch URL content and extract text if possible."""
        try:
            from logicore.tools.web import _validate_url_safety
            import httpx
            
            is_safe, reason = _validate_url_safety(url)
            if not is_safe:
                return None
            
            with httpx.Client(follow_redirects=True, timeout=20.0) as client:
                response = client.get(url)
                response.raise_for_status()
                content_type = (response.headers.get("content-type") or "").lower()
                
                if "text/" in content_type or "json" in content_type or "xml" in content_type:
                    return response.text
                
                parsed = urlparse(url)
                _, ext = os.path.splitext(parsed.path)
                if not ext:
                    return None
                
                fd, temp_path = tempfile.mkstemp(suffix=ext)
                try:
                    os.chmod(temp_path, 0o600)
                    with os.fdopen(fd, 'wb') as tmp:
                        tmp.write(response.content)
                    return self._extract_text_from_local_file(temp_path)
                finally:
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Could not extract text from URL: {e}")
            return None
    
    def enrich(self, user_input: Union[str, List[Dict[str, Any]]]) -> Union[str, List[Dict[str, Any]]]:
        """
        Enrich user input by resolving references.
        
        - Detects URLs and file paths in text
        - Attaches images as multimodal content
        - Extracts text from documents and injects as context
        
        Returns enriched input (string or multimodal list).
        """
        if not isinstance(user_input, str):
            return user_input
        
        refs = self._extract_references_from_text(user_input)
        if not refs:
            return user_input
        
        image_refs: List[str] = []
        context_chunks: List[str] = []
        cleaned_text = user_input
        
        max_sources = 4
        max_chars_per_source = 1800
        processed = 0
        
        for ref in refs:
            if processed >= max_sources:
                break
            
            is_url = ref.startswith(("http://", "https://"))
            local_path = self._resolve_local_reference(ref) if not is_url else None
            
            # Handle image references
            if self._is_image_reference(ref):
                if is_url:
                    image_refs.append(ref)
                    cleaned_text = cleaned_text.replace(ref, " ")
                    processed += 1
                    continue
                if local_path:
                    image_refs.append(local_path)
                    cleaned_text = cleaned_text.replace(ref, " ")
                    processed += 1
                    continue
            
            # Extract text from document/URL
            extracted = None
            source_label = ref
            if local_path:
                extracted = self._extract_text_from_local_file(local_path)
                source_label = local_path
            elif is_url:
                extracted = self._extract_text_from_url(ref)
            
            if extracted:
                snippet = extracted.strip()
                if len(snippet) > max_chars_per_source:
                    snippet = snippet[:max_chars_per_source] + "\n...[truncated]"
                context_chunks.append(f"Source: {source_label}\n{snippet}")
                cleaned_text = cleaned_text.replace(ref, " ")
                processed += 1
        
        cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()
        if not cleaned_text:
            cleaned_text = "Please analyze the attached references and answer the user request."
        
        if context_chunks:
            cleaned_text += "\n\n<auto_reference_context>\n" + "\n\n---\n\n".join(context_chunks) + "\n</auto_reference_context>"
        
        if image_refs:
            parts: List[Dict[str, Any]] = [{"type": "text", "text": cleaned_text}]
            for image_ref in image_refs:
                parts.append({"type": "image_url", "image_url": {"url": image_ref}})
            if self.debug:
                logger.debug(f"[InputEnricher] Enriched input: {len(image_refs)} image(s), {len(context_chunks)} text source(s)")
            return parts
        
        if context_chunks and self.debug:
            logger.debug(f"[InputEnricher] Enriched input with {len(context_chunks)} text source(s)")
        
        return cleaned_text
    
    async def enrich_async(self, user_input: Union[str, List[Dict[str, Any]]]) -> Union[str, List[Dict[str, Any]]]:
        """Async wrapper to prevent blocking on document parsing."""
        if not isinstance(user_input, str):
            return user_input
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.enrich, user_input)
