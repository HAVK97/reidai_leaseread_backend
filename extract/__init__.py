"""ReidAI extraction stage.

Consumes a completed preextract_outputs/<doc_id>/ directory and runs
LLM field extraction per group, writing auditable JSON under
preextract_outputs/<doc_id>/extractions/<run_id>/<group_id>.json.

Usage:
  python -m extract run   --dir preextract_outputs/Henry_Unit_2 --group rent_review_header
  python -m extract pack  --dir preextract_outputs/Henry_Unit_2 --group rent_review_header
  python -m extract list-groups

Gemini: set GEMINI_API_KEY or GOOGLE_API_KEY (see extract.providers.GeminiProvider).
OpenAI: OPENAI_API_KEY. Anthropic: ANTHROPIC_API_KEY.
"""

from .artefacts import ArtefactLoader
from .assembler import EvidencePackAssembler
from .groups import GroupConfig, load_group_config
from .providers import build_provider, LLMProvider, LLMRequest, LLMResponse
from .resolver import QuoteResolver
from .runner import PromptRunner

__all__ = [
    "ArtefactLoader",
    "EvidencePackAssembler",
    "GroupConfig",
    "load_group_config",
    "build_provider",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "QuoteResolver",
    "PromptRunner",
]
