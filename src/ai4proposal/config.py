"""Configuration from environment variables. No hardcoded keys."""
import os


def get_llm_config() -> dict:
    """Get LLM configuration from environment."""
    return {
        "api_key": os.environ.get("AI4PROPOSAL_API_KEY", ""),
        "base_url": os.environ.get("AI4PROPOSAL_BASE_URL", "https://api.openai.com/v1"),
        "model": os.environ.get("AI4PROPOSAL_MODEL", "gpt-4.1"),
        "timeout": int(os.environ.get("AI4PROPOSAL_TIMEOUT_SECONDS", "300")),
    }


def get_image_config() -> dict:
    """Get image generation configuration."""
    return {
        "api_key": os.environ.get("AI4PROPOSAL_IMAGE_API_KEY", os.environ.get("AI4PROPOSAL_API_KEY", "")),
        "model": os.environ.get("AI4PROPOSAL_IMAGE_MODEL", "gpt-image-2"),
    }


def get_eval_config() -> dict:
    """Get evaluation LLM config (defaults to main LLM)."""
    return {
        "api_key": os.environ.get("AI4PROPOSAL_EVAL_API_KEY") or os.environ.get("AI4PROPOSAL_API_KEY", ""),
        "model": os.environ.get("AI4PROPOSAL_EVAL_MODEL") or os.environ.get("AI4PROPOSAL_MODEL", "gpt-4.1"),
        "base_url": os.environ.get("AI4PROPOSAL_EVAL_BASE_URL") or os.environ.get("AI4PROPOSAL_BASE_URL", ""),
    }
