from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

import httpx


class ProviderError(RuntimeError):
    pass


def extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1]).strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].lstrip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ProviderError("模型没有返回 JSON 对象") from exc
        try:
            value = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as nested:
            raise ProviderError("模型返回的 JSON 无法解析") from nested
    if not isinstance(value, dict):
        raise ProviderError("模型返回值必须是 JSON 对象")
    return value


class AIProvider(ABC):
    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self.api_key = api_key
        self.base_url = base_url

    @abstractmethod
    async def text(self, model: str, system: str, prompt: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def structured(
        self, model: str, system: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError


class OpenAIProvider(AIProvider):
    async def _request(self, body: dict[str, Any]) -> dict[str, Any]:
        url = (self.base_url or "https://api.openai.com/v1").rstrip("/") + "/responses"
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )
        if response.is_error:
            raise ProviderError(f"OpenAI 请求失败 ({response.status_code})：{response.text[:500]}")
        return response.json()

    @staticmethod
    def _output_text(response: dict[str, Any]) -> str:
        if isinstance(response.get("output_text"), str):
            return response["output_text"]
        texts: list[str] = []
        for item in response.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    texts.append(content["text"])
        if not texts:
            raise ProviderError("OpenAI 响应中没有文本")
        return "".join(texts)

    async def text(self, model: str, system: str, prompt: str) -> str:
        response = await self._request(
            {"model": model, "instructions": system, "input": prompt}
        )
        return self._output_text(response)

    async def structured(
        self, model: str, system: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        response = await self._request(
            {
                "model": model,
                "instructions": system,
                "input": prompt,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "roll_proposal",
                        "strict": True,
                        "schema": schema,
                    }
                },
            }
        )
        return extract_json(self._output_text(response))


class AnthropicProvider(AIProvider):
    async def _request(self, body: dict[str, Any]) -> dict[str, Any]:
        url = (self.base_url or "https://api.anthropic.com/v1").rstrip("/") + "/messages"
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            )
        if response.is_error:
            raise ProviderError(f"Claude 请求失败 ({response.status_code})：{response.text[:500]}")
        return response.json()

    async def text(self, model: str, system: str, prompt: str) -> str:
        response = await self._request(
            {
                "model": model,
                "max_tokens": 8192,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            }
        )
        texts = [item["text"] for item in response.get("content", []) if item.get("type") == "text"]
        if not texts:
            raise ProviderError("Claude 响应中没有文本")
        return "".join(texts)

    async def structured(
        self, model: str, system: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        response = await self._request(
            {
                "model": model,
                "max_tokens": 8192,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
                "tools": [
                    {
                        "name": "submit_roll_proposal",
                        "description": "提交结构化且不包含虚构随机结果的投骰方案",
                        "input_schema": schema,
                    }
                ],
                "tool_choice": {"type": "tool", "name": "submit_roll_proposal"},
            }
        )
        for item in response.get("content", []):
            if item.get("type") == "tool_use" and item.get("name") == "submit_roll_proposal":
                return item["input"]
        raise ProviderError("Claude 没有提交结构化投骰方案")


class PoeProvider(AIProvider):
    async def _request(self, model: str, system: str, prompt: str) -> str:
        url = (self.base_url or "https://api.poe.com/v1").rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                },
            )
        if response.is_error:
            raise ProviderError(f"Poe 请求失败 ({response.status_code})：{response.text[:500]}")
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Poe 响应格式不正确") from exc

    async def text(self, model: str, system: str, prompt: str) -> str:
        return await self._request(model, system, prompt)

    async def structured(
        self, model: str, system: str, prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        schema_text = json.dumps(schema, ensure_ascii=False)
        text = await self._request(
            model,
            system + "\n只返回一个 JSON 对象，不要使用 Markdown 代码块。",
            f"{prompt}\n\n必须符合此 JSON Schema：\n{schema_text}",
        )
        return extract_json(text)
