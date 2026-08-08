"""Reusable AI service for the Qinance marketplace backend.

Logs through campaigns.AILog. Caches responses with Django's cache.
"""

import logging
import time

from django.core.cache import cache

from .config import AIConfig
from .gemini_provider import GeminiProvider

logger = logging.getLogger(__name__)


class AIService:
    """Reusable AI service that internally calls the configured provider."""

    _provider = None

    def __init__(self):
        self.provider = self._get_provider()

    @classmethod
    def _get_provider(cls):
        if cls._provider is None:
            provider_config = AIConfig.get_provider_config()
            cls._provider = GeminiProvider(
                model=provider_config['model'],
            )
        return cls._provider

    def generate(self, prompt, system_prompt=None, feature='general', user_role=None, temperature=None, max_tokens=None, tool_used=None, intent=None):
        start = time.time()
        result = self.provider.generate(
            prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        latency_ms = int((time.time() - start) * 1000)
        self._log_request(
            feature=feature,
            user_role=user_role,
            tokens=result.get('tokens', 0),
            latency_ms=result.get('latency_ms', latency_ms),
            success=result.get('success', False),
            error=result.get('error'),
            cache_hit=False,
            tool_used=tool_used,
            intent=intent,
            response_time=latency_ms,
        )
        return result

    def generate_json(self, prompt, system_prompt=None, feature='general', user_role=None, temperature=None, max_tokens=None, tool_used=None, intent=None):
        start = time.time()
        result = self.provider.generate_json(
            prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        latency_ms = int((time.time() - start) * 1000)
        self._log_request(
            feature=feature,
            user_role=user_role,
            tokens=result.get('tokens', 0),
            latency_ms=result.get('latency_ms', latency_ms),
            success=result.get('success', False),
            error=result.get('error'),
            cache_hit=False,
            tool_used=tool_used,
            intent=intent,
            response_time=latency_ms,
        )
        return result

    def embed(self, text, feature='embedding', user_role=None):
        start = time.time()
        result = self.provider.embed(text)
        latency_ms = int((time.time() - start) * 1000)
        self._log_request(
            feature=feature,
            user_role=user_role,
            tokens=0,
            latency_ms=latency_ms,
            success=result.get('success', False),
            error=result.get('error'),
            cache_hit=False,
            tool_used='embedding',
            intent='embedding',
            response_time=latency_ms,
        )
        return result

    def _log_request(self, feature, user_role, tokens, latency_ms, success, error=None, cache_hit=False, tool_used='', intent='', response_time=0):
        if not AIConfig.AI_LOGGING_ENABLED:
            return
        try:
            from campaigns.models import AILog
            AILog.objects.create(
                feature=feature,
                user_role=user_role or '',
                model=AIConfig.GEMINI_MODEL,
                provider=AIConfig.AI_PROVIDER,
                tokens=tokens,
                latency_ms=latency_ms,
                success=success,
                error=error or '',
                cache_hit=cache_hit,
                tool_used=tool_used or '',
                intent=intent or '',
                response_time=response_time,
            )
        except Exception as e:
            logger.warning('Failed to log AI request: %s', e)

    def get_cached_or_generate(self, cache_key, prompt, system_prompt=None, feature='general', user_role=None, ttl=None, tool_used=None, intent=None):
        ttl = ttl or AIConfig.AI_CACHE_TTL
        cached = cache.get(cache_key)
        if cached is not None:
            if AIConfig.AI_LOGGING_ENABLED:
                try:
                    from campaigns.models import AILog
                    AILog.objects.create(
                        feature=feature, user_role=user_role or '',
                        model=AIConfig.GEMINI_MODEL, provider=AIConfig.AI_PROVIDER,
                        tokens=0, latency_ms=0, success=True, cache_hit=True,
                        tool_used=tool_used or '', intent=intent or '', response_time=0,
                    )
                except Exception as e:
                    logger.warning('Failed to log AI cache hit: %s', e)
            return {**cached, 'cached': True}

        result = self.generate(
            prompt, system_prompt=system_prompt, feature=feature,
            user_role=user_role, tool_used=tool_used, intent=intent,
        )
        result['cached'] = False
        if result['success']:
            cache.set(cache_key, result, timeout=ttl)
        return result

    def get_cached_or_generate_json(self, cache_key, prompt, system_prompt=None, feature='general', user_role=None, ttl=None, tool_used=None, intent=None):
        ttl = ttl or AIConfig.AI_CACHE_TTL
        cached = cache.get(cache_key)
        if cached is not None:
            if AIConfig.AI_LOGGING_ENABLED:
                try:
                    from campaigns.models import AILog
                    AILog.objects.create(
                        feature=feature, user_role=user_role or '',
                        model=AIConfig.GEMINI_MODEL, provider=AIConfig.AI_PROVIDER,
                        tokens=0, latency_ms=0, success=True, cache_hit=True,
                        tool_used=tool_used or '', intent=intent or '', response_time=0,
                    )
                except Exception as e:
                    logger.warning('Failed to log AI cache hit: %s', e)
            return {**cached, 'cached': True}

        result = self.generate_json(
            prompt, system_prompt=system_prompt, feature=feature,
            user_role=user_role, tool_used=tool_used, intent=intent,
        )
        result['cached'] = False
        if result['success']:
            cache.set(cache_key, result, timeout=ttl)
        return result