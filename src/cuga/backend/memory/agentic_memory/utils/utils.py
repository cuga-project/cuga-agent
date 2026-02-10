import re

from dotenv import load_dotenv
from pymilvus import MilvusClient
from sentence_transformers import SentenceTransformer

from cuga.backend.memory.agentic_memory.utils.logging import Logging

load_dotenv()

logger = Logging.get_logger()


def clean_llm_response(content: str) -> str:
    """
    Removes common junk from an LLM response so that it can be parsed using `json.loads()`

    Actions:
    - Returns the inner content of a Markdown code block.
    - If Markdown code blocks are not present, remove thought and reasoning blocks entirely.
    """
    pattern = r"^```[a-zA-Z0-9]*\n(.*?)\n```$"
    match = re.match(pattern, content.strip(), flags=re.MULTILINE | re.DOTALL)
    match_res = match.group(1).strip() if match else content.strip()
    return re.sub(
        r"<(?:think(?:ing)?|reflection)>.*?</(?:think(?:ing)?|reflection)>", "", match_res, flags=re.DOTALL
    ).strip()


def get_milvus_client() -> MilvusClient:
    # Resolve the Milvus URI from memory config so local DB files land under DBS_DIR
    # (or a caller-provided env/config override), instead of the process cwd.
    from cuga.backend.memory.agentic_memory.config import config as memory_config

    milvus_uri = memory_config["vector_store"]["config"]["url"]
    return MilvusClient(uri=milvus_uri)


def get_chat_model(model_settings, max_tokens=None):
    """Create LLM instance using the unified LLMManager (compat wrapper).

    Note: Deprecated. Prefer using `LLMManager().get_model(model_settings, max_tokens)` directly.
    """
    # Lazy import to avoid circulars and keep compatibility
    from cuga.backend.llm.models import LLMManager

    # Determine max_tokens: prefer explicit arg, then settings, else keep legacy default 5000
    effective_max_tokens = max_tokens if max_tokens is not None else model_settings.get('max_tokens', 5000)

    # Ensure compatibility with LLMManager expecting objects that may implement to_dict()
    if isinstance(model_settings, dict) and not hasattr(model_settings, 'to_dict'):

        class _ModelSettingsWrapper(dict):
            def to_dict(self):
                return dict(self)

        model_settings = _ModelSettingsWrapper(model_settings)

    # Add max_tokens to model_settings if not already present
    if 'max_tokens' not in model_settings:
        model_settings['max_tokens'] = effective_max_tokens

    manager = LLMManager()
    return manager.get_model(model_settings)


def get_embedding_model(model_name) -> SentenceTransformer:
    return SentenceTransformer(model_name)
