"""HTTP client for OpenAI-compatible chat + embeddings (local Ollama, llama.cpp, remote gateway)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Optional

import aiohttp

from ngram.cognition import gemma
from ngram.inference.types import ChatCompletionResult, ToolCallSpec


class OpenAICompatibleTransport:
    """Low-level /v1 calls with Gemma-specific message shaping (thinking channel)."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        *,
        api_path: str = "/v1",
        gemma_shaping: bool = True,
        max_tokens_field: str = "max_tokens",
        pass_temperature: bool = True,
        timeout: float = 120.0,
        retry_attempts: int = 3,
        retry_delay: float = 2.0,
        extra_headers: dict[str, str] | None = None,
        embedding_dimensions: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        clean_api_path = (api_path or "").strip()
        if clean_api_path and not clean_api_path.startswith("/"):
            clean_api_path = f"/{clean_api_path}"
        self.api = f"{self.base_url}{clean_api_path.rstrip('/')}"
        self.gemma_shaping = bool(gemma_shaping)
        self.max_tokens_field = max_tokens_field or "max_tokens"
        self.pass_temperature = bool(pass_temperature)
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.extra_headers = dict(extra_headers or {})
        self.embedding_dimensions = (
            int(embedding_dimensions) if embedding_dimensions and int(embedding_dimensions) > 0 else None
        )
        self._session: aiohttp.ClientSession | None = None

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers=self.extra_headers or None,
            )
        return self._session

    async def close(self) -> None:
        s = self._session
        self._session = None
        if s is not None and not s.closed:
            try:
                await s.close()
            except BaseException:
                pass
        await asyncio.sleep(0)

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = await self._session_get()
        url = f"{self.api}{path}"
        last_err: Exception | None = None
        for attempt in range(self.retry_attempts):
            try:
                async with session.post(url, json=payload) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        raise aiohttp.ClientResponseError(
                            resp.request_info,
                            resp.history,
                            status=resp.status,
                            message=text[:500],
                        )
                    return json.loads(text) if text else {}
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = e
                if attempt + 1 < self.retry_attempts:
                    await asyncio.sleep(self.retry_delay * (2**attempt))
        raise last_err or RuntimeError("request failed")

    async def _get_json(self, path: str) -> dict[str, Any]:
        session = await self._session_get()
        url = f"{self.api}{path}"
        last_err: Exception | None = None
        for attempt in range(self.retry_attempts):
            try:
                async with session.get(url) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        raise aiohttp.ClientResponseError(
                            resp.request_info,
                            resp.history,
                            status=resp.status,
                            message=text[:500],
                        )
                    return json.loads(text) if text else {}
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = e
                if attempt + 1 < self.retry_attempts:
                    await asyncio.sleep(self.retry_delay * (2**attempt))
        raise last_err or RuntimeError("request failed")

    async def list_models(self) -> list[str]:
        data = await self._get_json("/models")
        models = data.get("data") or []
        return [m.get("id", "") for m in models if m.get("id")]

    async def ensure_models(self, *names: str) -> tuple[bool, list[str]]:
        listed = await self.list_models()
        available = set(listed)
        missing: list[str] = []
        for n in names:
            if not n:
                continue
            if n in available:
                continue
            if any(m == n or m.startswith(f"{n}:") for m in listed):
                continue
            missing.append(n)
        return (len(missing) == 0, missing)

    def _build_chat_completions_payload(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int | None = 1024,
        think: bool = False,
        stream: bool = False,
        num_ctx: Optional[int] = None,
    ) -> dict[str, Any]:
        from ngram.inference.visual_results import chat_messages
        messages = chat_messages(messages)
        messages = [
            {**message, 'content': [
                {'type': 'file', 'file': {k: block[k] for k in ('filename', 'file_data', 'file_id') if k in block}}
                if isinstance(block, dict) and block.get('type') == 'input_file' else block
                for block in message['content']
            ]} if isinstance(message.get('content'), list) else message
            for message in messages
        ]
        if self.gemma_shaping:
            sys_parts: list[str] = []
            rest: list[dict[str, Any]] = []
            for m in messages:
                if m.get("role") == "system":
                    c = m.get("content") or ""
                    if isinstance(c, list):
                        c = gemma.stringify_content_blocks(c)
                    sys_parts.append(str(c))
                else:
                    rest.append(m)
            system_merged = "\n\n".join(sys_parts) if sys_parts else None
            outgoing_messages: list[dict[str, Any]] = []
            if system_merged:
                inner = gemma.inject_thinking_instruction(system_merged, think)
                if not think:
                    inner = inner + "\n" + gemma.empty_thought_channel_fragment()
                outgoing_messages.append({"role": "system", "content": inner})
            outgoing_messages.extend(rest)
        else:
            outgoing_messages = [dict(message) for message in messages]

        payload: dict[str, Any] = {
            "model": model,
            "messages": outgoing_messages,
            "stream": stream,
        }
        if self.pass_temperature:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload[self.max_tokens_field] = max_tokens
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        if num_ctx is not None and int(num_ctx) > 0:
            payload["options"] = {"num_ctx": int(num_ctx)}
        return payload

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int | None = 1024,
        think: bool = False,
        stream: bool = False,
        num_ctx: Optional[int] = None,
    ) -> ChatCompletionResult | AsyncIterator[str]:
        payload = self._build_chat_completions_payload(
            model,
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            think=think,
            stream=stream,
            num_ctx=num_ctx,
        )

        if stream:
            return self._chat_stream(payload)
        data = await self._post_json("/chat/completions", payload)
        return self._parse_chat_response(data)

    async def _chat_stream(self, payload: dict[str, Any]) -> AsyncIterator[str]:
        session = await self._session_get()
        url = f"{self.api}/chat/completions"
        payload = {**payload, "stream": True}
        async with session.post(url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.content:
                if not line:
                    continue
                s = line.decode("utf-8", errors="replace").strip()
                if not s.startswith("data:"):
                    continue
                chunk = s[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                    delta = (obj.get("choices") or [{}])[0].get("delta") or {}
                    if delta.get("content"):
                        yield delta["content"]
                except json.JSONDecodeError:
                    continue

    def _parse_chat_response(self, data: dict[str, Any]) -> ChatCompletionResult:
        choices = data.get("choices") or []
        if not choices:
            return ChatCompletionResult(content="")
        msg = choices[0].get("message") or {}
        raw_content = msg.get("content") or ""
        if isinstance(raw_content, list):
            text = gemma.stringify_content_blocks(raw_content)
        else:
            text = str(raw_content)

        reasoning_text = ""
        for key in ("reasoning_content", "reasoning"):
            v = msg.get(key)
            if isinstance(v, str) and v.strip():
                reasoning_text = v.strip()
                break

        vis_raw, plaintext_cot = gemma.separate_plaintext_chain_of_thought(text)
        parsed = gemma.parse_assistant_output(vis_raw)
        visible = parsed.visible_user_text.strip()
        if not visible and vis_raw.strip():
            loose = gemma.strip_thinking_for_history(vis_raw).strip()
            if loose:
                visible = loose

        thinking_parts: list[str] = []
        if parsed.thinking:
            thinking_parts.append(parsed.thinking)
        if plaintext_cot:
            thinking_parts.append(plaintext_cot)
        if reasoning_text:
            thinking_parts.append(reasoning_text)
        thinking: str | None = "\n\n".join(thinking_parts) if thinking_parts else None
        tool_calls: list[ToolCallSpec] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            tid = str(tc.get("id") or gemma.new_tool_call_id())
            args_raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {}
            if name:
                tool_calls.append(
                    ToolCallSpec(
                        name=name,
                        arguments=args if isinstance(args, dict) else {},
                        id=tid,
                    )
                )
        if not tool_calls:
            for raw in gemma.tool_calls_from_parsed(parsed):
                tool_calls.append(
                    ToolCallSpec(
                        name=raw["name"],
                        arguments=raw.get("arguments") or {},
                        id=gemma.new_tool_call_id(),
                    )
                )
        usage = data.get("usage") or {}
        u = {}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if k in usage:
                u[k] = int(usage[k])
        return ChatCompletionResult(
            content=visible,
            thinking=thinking,
            tool_calls=tool_calls,
            finish_reason=(choices[0].get("finish_reason")),
            raw_message=msg,
            usage=u,
            raw_assistant_text=text,
        )

    async def embed(self, model: str, text: str) -> list[float]:
        payload: dict[str, Any] = {"model": model, "input": text}
        if self.embedding_dimensions is not None:
            payload["dimensions"] = self.embedding_dimensions
        data = await self._post_json(
            "/embeddings",
            payload,
        )
        emb = (data.get("data") or [{}])[0].get("embedding")
        if not emb:
            return []
        return [float(x) for x in emb]


class OpenAIResponsesTransport(OpenAICompatibleTransport):
    """Native OpenAI Responses API behind ngram's provider-neutral contract.

    The harness deliberately keeps its internal rolling history in the common
    Chat Completions shape.  This adapter converts that history, function
    declarations, calls, and outputs at the HTTP boundary so the rest of the
    Entity loop remains provider independent.
    """

    @staticmethod
    def _response_tool(tool: dict[str, Any]) -> dict[str, Any] | None:
        if tool.get("type") != "function":
            return dict(tool)
        fn = tool.get("function") or {}
        name = str(fn.get("name") or "").strip()
        if not name:
            return None
        out: dict[str, Any] = {
            "type": "function",
            "name": name,
            "description": str(fn.get("description") or ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            # Keep optional harness arguments optional; Responses otherwise
            # normalizes compatible schemas into strict, all-required schemas.
            "strict": bool(fn.get("strict", False)),
        }
        return out

    @staticmethod
    def _message_content(content: Any, *, assistant: bool = False) -> Any:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content or "")
        if assistant:
            return gemma.stringify_content_blocks(content)
        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                blocks.append({"type": "input_text", "text": str(block)})
                continue
            kind = str(block.get("type") or "")
            if kind in ("text", "input_text", "output_text"):
                blocks.append({"type": "input_text", "text": str(block.get("text") or "")})
            elif kind in ("image_url", "input_image"):
                raw = block.get("image_url")
                url = raw.get("url") if isinstance(raw, dict) else raw
                if not url:
                    url = block.get("url")
                if url:
                    image: dict[str, Any] = {"type": "input_image", "image_url": str(url)}
                    detail = block.get("detail") or (raw.get("detail") if isinstance(raw, dict) else None)
                    if detail:
                        image["detail"] = detail
                    blocks.append(image)
            elif kind == "input_file":
                blocks.append(dict(block))
            elif kind == 'input_audio':
                raise ValueError('Responses audio input needs transcription before inference')
            else:
                blocks.append({"type": "input_text", "text": json.dumps(block, ensure_ascii=False)})
        return blocks

    def _responses_input(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from ngram.inference.visual_results import VisualResult, content_blocks
        items: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "user")
            if role == "tool":
                call_id = str(message.get("tool_call_id") or "").strip()
                if call_id:
                    output = message.get("content", "")
                    if isinstance(output, VisualResult):
                        output = self._message_content(content_blocks(output))
                    elif not isinstance(output, str):
                        output = json.dumps(output, ensure_ascii=False)
                    items.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": output,
                    })
                continue

            content = self._message_content(
                message.get("content", ""),
                assistant=role == "assistant",
            )
            if content:
                items.append({"role": role, "content": content})

            if role == "assistant":
                for call in message.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    call_id = str(call.get("id") or "").strip()
                    name = str(fn.get("name") or "").strip()
                    if call_id and name:
                        arguments = fn.get("arguments") or "{}"
                        if not isinstance(arguments, str):
                            arguments = json.dumps(arguments, ensure_ascii=False)
                        items.append({
                            "type": "function_call",
                            "call_id": call_id,
                            "name": name,
                            "arguments": arguments,
                        })
        return items

    def _build_responses_payload(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        max_tokens: int | None = 1024,
        think: bool = False,
        stream: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "input": self._responses_input(messages),
            "store": False,
            "stream": stream,
        }
        if max_tokens is not None:
            payload["max_output_tokens"] = max_tokens
        if model == "gpt-6-astra" or model.startswith("gpt-6-astra-"):
            payload["reasoning"] = {"effort": "medium" if think else "low"}
            # Responses counts reasoning toward the output cap. Tiny classifiers
            # still need room to reason before emitting their short visible answer.
            if max_tokens is not None:
                payload["max_output_tokens"] = max(max_tokens, 2048 if think else 1024)
        elif model.startswith(("gpt-5.6", "gpt-6")):
            payload["reasoning"] = {"effort": "medium" if think else "none"}
        if tools:
            converted = [self._response_tool(tool) for tool in tools]
            payload["tools"] = [tool for tool in converted if tool is not None]
            if tool_choice:
                payload["tool_choice"] = tool_choice
            payload["parallel_tool_calls"] = True
        return payload

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int | None = 1024,
        think: bool = False,
        stream: bool = False,
        num_ctx: Optional[int] = None,
    ) -> ChatCompletionResult | AsyncIterator[str]:
        del temperature, num_ctx
        payload = self._build_responses_payload(
            model,
            messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            think=think,
            stream=stream,
        )
        if stream:
            return self._responses_stream(payload)
        data = await self._post_json("/responses", payload)
        return self._parse_responses_response(data)

    async def _responses_stream(self, payload: dict[str, Any]) -> AsyncIterator[str]:
        session = await self._session_get()
        url = f"{self.api}/responses"
        async with session.post(url, json={**payload, "stream": True}) as resp:
            resp.raise_for_status()
            async for line in resp.content:
                text = line.decode("utf-8", errors="replace").strip()
                if not text.startswith("data:"):
                    continue
                raw = text[5:].strip()
                if raw == "[DONE]":
                    break
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "response.output_text.delta" and event.get("delta"):
                    yield str(event["delta"])

    @staticmethod
    def _parse_responses_response(data: dict[str, Any]) -> ChatCompletionResult:
        text_parts: list[str] = []
        tool_calls: list[ToolCallSpec] = []
        for item in data.get("output") or []:
            kind = item.get("type")
            if kind == "message":
                for block in item.get("content") or []:
                    if block.get("type") == "output_text" and block.get("text"):
                        text_parts.append(str(block["text"]))
                    elif block.get("type") == "refusal" and block.get("refusal"):
                        text_parts.append(str(block["refusal"]))
            elif kind == "function_call":
                raw_args = item.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCallSpec(
                    name=str(item.get("name") or ""),
                    arguments=args if isinstance(args, dict) else {},
                    id=str(item.get("call_id") or item.get("id") or gemma.new_tool_call_id()),
                ))

        text = "\n".join(part for part in text_parts if part).strip()
        usage = data.get("usage") or {}
        mapped_usage: dict[str, int] = {}
        for source, target in (
            ("input_tokens", "prompt_tokens"),
            ("output_tokens", "completion_tokens"),
            ("total_tokens", "total_tokens"),
        ):
            if source in usage:
                mapped_usage[target] = int(usage[source])
        incomplete = data.get("incomplete_details") or {}
        finish_reason = incomplete.get("reason") if data.get("status") == "incomplete" else "stop"
        raw_message = {
            "role": "assistant",
            "content": text,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in tool_calls
            ],
        }
        return ChatCompletionResult(
            content=text,
            thinking=None,
            tool_calls=tool_calls,
            finish_reason=str(finish_reason or "stop"),
            raw_message=raw_message,
            usage=mapped_usage,
            raw_assistant_text=text,
        )
