import os
from dotenv import load_dotenv

load_dotenv()


class AIConfig:
    """Central AI configuration loaded from environment variables."""

    # Provider selection: gemini (default), claude, openai, deepseek
    AI_PROVIDER = os.getenv('AI_PROVIDER', 'gemini')

    # Gemini settings
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
    GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.0-flash')

    # Generic provider settings (future providers)
    CLAUDE_API_KEY = os.getenv('CLAUDE_API_KEY', '')
    CLAUDE_MODEL = os.getenv('CLAUDE_MODEL', 'claude-3-5-sonnet-latest')

    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
    OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')

    DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY', '')
    DEEPSEEK_MODEL = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')

    # Generation settings
    TEMPERATURE = float(os.getenv('AI_TEMPERATURE', '0.3'))
    MAX_TOKENS = int(os.getenv('AI_MAX_TOKENS', '1024'))

    # Cache TTL for AI summaries (seconds) — default 24 hours
    AI_CACHE_TTL = int(os.getenv('AI_CACHE_TTL', '86400'))

    # Logging
    AI_LOGGING_ENABLED = os.getenv('AI_LOGGING_ENABLED', 'True') == 'True'

    @classmethod
    def get_provider_config(cls):
        """Return the config dict for the active provider."""
        provider = cls.AI_PROVIDER.lower()
        if provider == 'gemini':
            return {'provider': 'gemini', 'api_key': cls.GEMINI_API_KEY, 'model': cls.GEMINI_MODEL}
        elif provider == 'claude':
            return {'provider': 'claude', 'api_key': cls.CLAUDE_API_KEY, 'model': cls.CLAUDE_MODEL}
        elif provider == 'openai':
            return {'provider': 'openai', 'api_key': cls.OPENAI_API_KEY, 'model': cls.OPENAI_MODEL}
        elif provider == 'deepseek':
            return {'provider': 'deepseek', 'api_key': cls.DEEPSEEK_API_KEY, 'model': cls.DEEPSEEK_MODEL}
        return {'provider': 'gemini', 'api_key': cls.GEMINI_API_KEY, 'model': cls.GEMINI_MODEL}