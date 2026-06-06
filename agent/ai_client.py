"""
AI Client — Dual Provider with Auto-Failover
Google Gemini + Anthropic Claude

- الـ provider يُكشَف تلقائياً من شكل المفتاح
- عند فشل أي منهما (quota / network / error) → تحويل فوري للآخر
- Circuit Breaker: بعد 3 فشل متتالي يُغلق الـ provider 60 ثانية ثم يُعاد تجربته
- صفر تغييرات على باقي الكود — نفس الـ signature
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

import structlog

logger = structlog.get_logger(__name__)

GEMINI_MODEL = "gemini-flash-latest"
CLAUDE_MODEL = "claude-sonnet-4-6"

_GEMINI = "gemini"
_CLAUDE = "claude"


# ── Provider detection ───────────────────────────────────────────────────────

def _detect_provider(api_key: str) -> str:
    """Detect provider from API key prefix."""
    return _CLAUDE if api_key.startswith("sk-ant-") else _GEMINI


def _fallback(primary: str) -> tuple[str, str]:
    """Return (fallback_provider, fallback_key) from environment or Settings."""
    if primary == _GEMINI:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            try:
                from config import get_settings
                key = get_settings().ANTHROPIC_API_KEY
            except Exception:
                pass
        return _CLAUDE, key
    else:
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            try:
                from config import get_settings
                key = get_settings().GEMINI_API_KEY
            except Exception:
                pass
        return _GEMINI, key


# ── Circuit Breaker ──────────────────────────────────────────────────────────

class _CircuitBreaker:
    OPEN_AFTER = 3        # consecutive failures before opening circuit
    COOLDOWN_SEC = 60     # seconds before half-open probe

    def __init__(self) -> None:
        self._fails: dict[str, int] = {_GEMINI: 0, _CLAUDE: 0}
        self._opened_at: dict[str, float] = {}

    def available(self, provider: str) -> bool:
        opened = self._opened_at.get(provider)
        if not opened:
            return True
        # Half-open: allow one probe after cooldown
        if time.monotonic() - opened >= self.COOLDOWN_SEC:
            return True
        return False

    def ok(self, provider: str) -> None:
        self._fails[provider] = 0
        self._opened_at.pop(provider, None)

    def fail(self, provider: str) -> None:
        self._fails[provider] = self._fails.get(provider, 0) + 1
        if self._fails[provider] >= self.OPEN_AFTER and provider not in self._opened_at:
            self._opened_at[provider] = time.monotonic()
            logger.warning("ai.circuit_open", provider=provider, failures=self._fails[provider])


_cb = _CircuitBreaker()


# ── Gemini implementation ────────────────────────────────────────────────────

def _gemini_client(api_key: str):
    from google import genai
    return genai.Client(api_key=api_key)


def _clean_schema(schema: dict) -> dict:
    if not isinstance(schema, dict):
        return schema
    drop = {"$schema", "additionalProperties", "examples", "default", "title"}
    out = {k: v for k, v in schema.items() if k not in drop}
    if "properties" in out:
        out["properties"] = {k: _clean_schema(v) for k, v in out["properties"].items()}
    if "items" in out:
        out["items"] = _clean_schema(out["items"])
    return out


def _to_gemini_tools(tools: list[dict]):
    from google.genai import types as gt
    return [gt.Tool(function_declarations=[
        gt.FunctionDeclaration(
            name=t["name"],
            description=t.get("description", ""),
            parameters=_clean_schema(t.get("input_schema", {})),
        )
        for t in tools
    ])]


async def _gemini_text(api_key: str, system: str, user: str, max_tokens: int, model: str) -> str:
    from google.genai import types as gt
    client = _gemini_client(api_key)
    config = gt.GenerateContentConfig(
        max_output_tokens=max_tokens,
        system_instruction=system or None,
    )
    resp = await client.aio.models.generate_content(model=model, contents=user, config=config)
    return resp.text.strip() if resp.text else ""


async def _gemini_loop(
    api_key: str, system: str, user_message: str,
    tools: list[dict], tool_executor: Callable,
    max_iterations: int, model: str,
) -> dict[str, Any]:
    from google.genai import types as gt
    client = _gemini_client(api_key)
    config = gt.GenerateContentConfig(
        system_instruction=system or None,
        tools=_to_gemini_tools(tools),
        automatic_function_calling=gt.AutomaticFunctionCallingConfig(disable=True),
    )
    contents = [gt.Content(role="user", parts=[gt.Part(text=user_message)])]
    final_text = ""
    calls_made: list[str] = []
    iteration = 0

    for iteration in range(max_iterations):
        resp = await client.aio.models.generate_content(model=model, contents=contents, config=config)
        contents.append(resp.candidates[0].content)

        for part in resp.candidates[0].content.parts:
            if getattr(part, "text", None):
                final_text = part.text

        fn_calls = [
            p.function_call for p in resp.candidates[0].content.parts
            if getattr(p, "function_call", None)
        ]
        if not fn_calls:
            logger.debug("gemini.loop.done", iterations=iteration + 1)
            break

        fn_parts = []
        for fn in fn_calls:
            name, args = fn.name, dict(fn.args) if fn.args else {}
            calls_made.append(name)
            logger.info("gemini.tool_call", tool=name, args_keys=list(args.keys()))
            try:
                result = await tool_executor(name, args)
            except Exception as exc:
                result = {"error": str(exc)}
            fn_parts.append(gt.Part(
                function_response=gt.FunctionResponse(
                    name=name,
                    response={"result": json.dumps(result, ensure_ascii=False)},
                )
            ))
        contents.append(gt.Content(role="user", parts=fn_parts))

    return {"final_text": final_text, "tool_calls": calls_made, "iterations": iteration + 1}


# ── Claude / Anthropic implementation ───────────────────────────────────────

def _claude_client(api_key: str):
    import anthropic
    return anthropic.AsyncAnthropic(api_key=api_key)


async def _claude_text(api_key: str, system: str, user: str, max_tokens: int, model: str) -> str:
    client = _claude_client(api_key)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user}],
    }
    if system:
        kwargs["system"] = system
    resp = await client.messages.create(**kwargs)
    return resp.content[0].text.strip() if resp.content else ""


async def _claude_loop(
    api_key: str, system: str, user_message: str,
    tools: list[dict], tool_executor: Callable,
    max_iterations: int, model: str,
) -> dict[str, Any]:
    client = _claude_client(api_key)
    messages: list[dict] = [{"role": "user", "content": user_message}]
    final_text = ""
    calls_made: list[str] = []
    iteration = 0

    for iteration in range(max_iterations):
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 4096,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools  # Anthropic format: uses input_schema as-is

        resp = await client.messages.create(**kwargs)

        for block in resp.content:
            if getattr(block, "text", None):
                final_text = block.text

        if resp.stop_reason == "end_turn":
            logger.debug("claude.loop.done", iterations=iteration + 1)
            break

        tool_blocks = [b for b in resp.content if b.type == "tool_use"]
        if not tool_blocks:
            break

        # Append assistant turn (SDK objects are accepted directly)
        messages.append({"role": "assistant", "content": resp.content})

        tool_results = []
        for block in tool_blocks:
            calls_made.append(block.name)
            logger.info("claude.tool_call", tool=block.name, args_keys=list(block.input.keys()))
            try:
                result = await tool_executor(block.name, block.input)
            except Exception as exc:
                result = {"error": str(exc)}
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
        messages.append({"role": "user", "content": tool_results})

    return {"final_text": final_text, "tool_calls": calls_made, "iterations": iteration + 1}


# ── Internal helper ──────────────────────────────────────────────────────────

async def _run_with_failover(
    primary: str,
    primary_key: str,
    primary_fn,
    fallback_fn,
    fn_args: tuple,
    label: str,
) -> Any:
    """Try primary provider → on failure, switch to fallback instantly."""
    fallback_provider, fallback_key = _fallback(primary)

    # ── Primary attempt ──
    if _cb.available(primary):
        try:
            result = await primary_fn(primary_key, *fn_args)
            _cb.ok(primary)
            logger.debug(f"ai.{label}.ok", provider=primary)
            return result
        except Exception as exc:
            _cb.fail(primary)
            logger.warning(
                f"ai.{label}.primary_failed",
                provider=primary,
                fallback=fallback_provider,
                error=str(exc)[:150],
            )
    else:
        logger.debug(f"ai.{label}.circuit_open_skip", provider=primary)

    # ── Fallback attempt ──
    if not fallback_key:
        raise RuntimeError(
            f"Primary provider '{primary}' unavailable and no fallback key configured"
        )

    if _cb.available(fallback_provider):
        try:
            result = await fallback_fn(fallback_key, *fn_args)
            _cb.ok(fallback_provider)
            logger.info(f"ai.{label}.fallback_ok", provider=fallback_provider)
            return result
        except Exception as exc:
            _cb.fail(fallback_provider)
            logger.error(f"ai.{label}.both_failed", error=str(exc)[:150])
            raise RuntimeError(f"Both AI providers failed [{label}]: {exc}") from exc
    else:
        logger.error(f"ai.{label}.both_circuits_open", primary=primary, fallback=fallback_provider)
        raise RuntimeError(f"Both AI providers circuit-open (primary={primary}, fallback={fallback_provider})")


# ── Public API ───────────────────────────────────────────────────────────────

async def get_text(
    api_key: str,
    system: str,
    user: str,
    max_tokens: int = 2048,
    model: str = None,
) -> str:
    """
    One-shot text generation with automatic failover.
    api_key يُحدد الـ primary provider — الآخر يُجلب من env تلقائياً.
    """
    primary = _detect_provider(api_key)
    p_model = model or (GEMINI_MODEL if primary == _GEMINI else CLAUDE_MODEL)
    fb_model = GEMINI_MODEL if primary == _CLAUDE else CLAUDE_MODEL

    p_fn = _gemini_text if primary == _GEMINI else _claude_text
    fb_fn = _gemini_text if primary == _CLAUDE else _claude_text

    # Build per-provider args (model differs between primary and fallback)
    async def _primary(key, sys, usr, mt, _):
        return await p_fn(key, sys, usr, mt, p_model)

    async def _fallback_fn(key, sys, usr, mt, _):
        return await fb_fn(key, sys, usr, mt, fb_model)

    return await _run_with_failover(
        primary=primary,
        primary_key=api_key,
        primary_fn=_primary,
        fallback_fn=_fallback_fn,
        fn_args=(system, user, max_tokens, None),
        label="text",
    )


async def run_agentic_loop(
    api_key: str,
    system: str,
    user_message: str,
    tools: list[dict],
    tool_executor: Callable[[str, dict], Any],
    max_iterations: int = 10,
    model: str = None,
) -> dict[str, Any]:
    """
    Agentic loop with tool calling — automatic failover between Gemini and Claude.
    عند فشل الـ primary، يُعاد تشغيل الـ loop كاملاً مع الـ fallback.
    """
    primary = _detect_provider(api_key)
    p_model = model or (GEMINI_MODEL if primary == _GEMINI else CLAUDE_MODEL)
    fb_model = GEMINI_MODEL if primary == _CLAUDE else CLAUDE_MODEL

    p_fn = _gemini_loop if primary == _GEMINI else _claude_loop
    fb_fn = _gemini_loop if primary == _CLAUDE else _claude_loop

    async def _primary(key, sys, usr, tls, te, mi, _):
        return await p_fn(key, sys, usr, tls, te, mi, p_model)

    async def _fallback_fn(key, sys, usr, tls, te, mi, _):
        return await fb_fn(key, sys, usr, tls, te, mi, fb_model)

    return await _run_with_failover(
        primary=primary,
        primary_key=api_key,
        primary_fn=_primary,
        fallback_fn=_fallback_fn,
        fn_args=(system, user_message, tools, tool_executor, max_iterations, None),
        label="loop",
    )
