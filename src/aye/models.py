# models.py
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from enum import Enum


class LLMSource(Enum):
    """Enumeration for LLM response sources."""
    LOCAL = "local"
    API = "api"


@dataclass
class LLMResponse:
    """
    Standardized response format for LLM interactions.
    
    Attributes:
        summary: The text summary/response from the LLM
        updated_files: List of files to be updated with their content
        chat_id: Optional chat ID (only for API responses)
        source: Whether response came from local model or API
    """
    summary: str
    updated_files: List[Dict[str, Any]]
    chat_id: Optional[int] = None
    source: LLMSource = LLMSource.API