import json
from typing import Any

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from physics_reviewer.config import get_settings


class QwenClient:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.qwen_api_key:
            raise RuntimeError("QWEN_API_KEY is not configured.")
        self._settings = settings
        self._client = OpenAI(
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_base_url,
            timeout=settings.qwen_request_timeout_seconds,
            max_retries=0,
        )

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        return self._complete_json_with_retry(system, user)

    def _complete_json_with_retry(self, system: str, user: str) -> dict[str, Any]:
        retrying = retry(
            wait=wait_exponential(multiplier=1, min=1, max=8),
            stop=stop_after_attempt(max(1, self._settings.qwen_retry_attempts)),
            reraise=True,
        )
        return retrying(self._complete_json_once)(system, user)

    def _complete_json_once(self, system: str, user: str) -> dict[str, Any]:
        response = self._client.chat.completions.create(
            model=self._settings.qwen_model,
            temperature=self._settings.qwen_temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
