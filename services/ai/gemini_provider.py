import json
import time
import logging
import google.genai as genai

from .config import AIConfig

logger = logging.getLogger(__name__)


class GeminiProvider:
    """Gemini API provider implementation for the Qinance marketplace backend."""

    def __init__(self, model=None):
        self.client = genai.Client(
            enterprise=True,
            project="project-dc3f5fcd-73e6-4b41-989",
            location="us-central1"
        )
        self.model = "gemini-2.5-flash"

    def generate(self, prompt, system_prompt=None, temperature=None, max_tokens=None):
        """Send a prompt to Gemini and return the text response."""
        start = time.time()
        try:
            contents = []
            if system_prompt:
                contents.append({'role': 'user', 'parts': [{'text': system_prompt}]})
            contents.append({'role': 'user', 'parts': [{'text': prompt}]})

            response = self.client.models.generate_content(
                model=self.model,
                contents=contents
            )

            text = response.text
            tokens = 0  # Not easily available with the new client

            latency_ms = int((time.time() - start) * 1000)

            return {
                'text': text.strip(),
                'tokens': tokens,
                'latency_ms': latency_ms,
                'success': True,
                'error': None,
            }

        except Exception as e:
            latency_ms = int((time.time() - start) * 1000)
            logger.error('Gemini API error: %s', e)
            return {
                'text': '',
                'tokens': 0,
                'latency_ms': latency_ms,
                'success': False,
                'error': str(e),
            }

    def generate_json(self, prompt, system_prompt=None, temperature=None, max_tokens=None):
        """Send a prompt to Gemini and parse the JSON response."""
        result = self.generate(
            prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if not result['success']:
            return {
                'data': None,
                'tokens': result['tokens'],
                'latency_ms': result['latency_ms'],
                'success': False,
                'error': result['error'],
            }

        try:
            text = result['text']
            if '```' in text:
                import re
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
                if match:
                    text = match.group(1)
            data = json.loads(text)
            return {
                'data': data,
                'tokens': result['tokens'],
                'latency_ms': result['latency_ms'],
                'success': True,
                'error': None,
            }
        except json.JSONDecodeError as e:
            logger.error('Failed to parse Gemini JSON response: %s', e)
            return {
                'data': None,
                'tokens': result['tokens'],
                'latency_ms': result['latency_ms'],
                'success': False,
                'error': f'Failed to parse AI response as JSON: {e}',
            }

    def embed(self, text):
        """Generate a semantic vector embedding using Gemini (768 dims)."""
        try:
            response = self.client.models.embed_content(
                model="text-embedding-004",
                contents=text
            )
            # Response contains a list of embeddings.
            embedding = response.embeddings[0].values
            return {'success': True, 'embedding': embedding}
        except Exception as e:
            logger.error('Gemini Embedding error: %s', e)
            return {'success': False, 'error': str(e)}
