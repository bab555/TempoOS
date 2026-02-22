# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
ContextBuilder — LLM context window management.

Reads full conversation history from ChatStore and constructs a
token-budget-aware messages array for the LLM. Two strategies:

  V1 (rule-based trim):
    - Keep system prompt + recent N rounds in full
    - Discard tool_call/tool_result from older rounds, keep only
      user/assistant text messages

  V2 (LLM summary):
    - When history exceeds a threshold, call a lightweight model
      (qwen3.5-plus) to compress early conversation into a summary
    - Cache the summary in Blackboard (_chat_summary)
    - Subsequent requests reuse the cached summary until new messages
      push it past the threshold again

The builder is called by Agent Controller on every request to produce
the optimal LLM context from the full history.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from tempo_os.memory.blackboard import TenantBlackboard
from tempo_os.memory.chat_store import ChatMessage, ChatStore

logger = logging.getLogger("tempo.context_builder")

SUMMARY_PROMPT = """你是一个对话摘要助手。请将以下对话历史压缩为一段简洁的摘要。

要求：
1. 保留关键信息：用户的核心需求、已完成的操作、重要的数据结论
2. 保留上下文：用户提到的产品名称、公司名称、数量、金额等具体信息
3. 丢弃冗余：工具调用的中间过程、重复的确认对话
4. 摘要长度控制在 300 字以内
5. 用第三人称描述（"用户要求..."、"系统已完成..."）

对话历史：
"""


class ContextBuilder:
    """
    Builds LLM-ready messages from ChatStore history + system prompt.

    Usage:
        builder = ContextBuilder(chat_store, blackboard, settings)
        messages = await builder.build(session_id, system_prompt)
    """

    def __init__(
        self,
        chat_store: ChatStore,
        blackboard: TenantBlackboard,
        *,
        max_recent_rounds: int = 6,
        summary_threshold: int = 10,
        summary_model: str = "qwen3.5-plus",
        api_key: str = "",
    ) -> None:
        self._chat_store = chat_store
        self._blackboard = blackboard
        self._max_recent_rounds = max_recent_rounds
        self._summary_threshold = summary_threshold
        self._summary_model = summary_model
        self._api_key = api_key

    async def build(
        self,
        session_id: str,
        system_prompt: str,
    ) -> List[Dict[str, Any]]:
        """
        Build LLM messages array from stored chat history.

        Returns: [system_prompt, (summary)?, recent_messages...]
        """
        all_messages = await self._chat_store.get_all(session_id)

        if not all_messages:
            return [{"role": "system", "content": system_prompt}]

        total = len(all_messages)

        # Split into "old" and "recent"
        recent_boundary = self._find_recent_boundary(all_messages)
        old_messages = all_messages[:recent_boundary]
        recent_messages = all_messages[recent_boundary:]

        llm_msgs: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

        # Handle old messages: summarize or trim
        if old_messages:
            if total >= self._summary_threshold and self._api_key:
                summary = await self._get_or_create_summary(
                    session_id, old_messages, total,
                )
                if summary:
                    llm_msgs.append({
                        "role": "system",
                        "content": f"[对话历史摘要]\n{summary}",
                    })
            else:
                trimmed = self._v1_trim(old_messages)
                llm_msgs.extend(trimmed)

        # Append recent messages in full
        for msg in recent_messages:
            llm_msgs.append(msg.to_llm_message())

        return llm_msgs

    def _find_recent_boundary(self, messages: List[ChatMessage]) -> int:
        """
        Find the index that separates "old" from "recent" messages.

        Keeps the last N user-assistant round-trips in the "recent" portion.
        A "round" is defined as a user message followed by any number of
        assistant/tool messages until the next user message.
        """
        user_indices = [
            i for i, m in enumerate(messages) if m.role == "user"
        ]

        if len(user_indices) <= self._max_recent_rounds:
            return 0  # Everything is "recent"

        boundary_user_idx = user_indices[-self._max_recent_rounds]
        return boundary_user_idx

    def _v1_trim(self, old_messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        """
        V1 rule-based trim: keep only user/assistant text from old messages,
        discard tool_call and tool_result intermediate steps.
        """
        trimmed: List[Dict[str, Any]] = []
        for msg in old_messages:
            if msg.role in ("user", "assistant") and msg.type == "text":
                content = msg.content
                if len(content) > 200:
                    content = content[:200] + "..."
                trimmed.append({"role": msg.role, "content": content})
        return trimmed

    async def _get_or_create_summary(
        self,
        session_id: str,
        old_messages: List[ChatMessage],
        total_count: int,
    ) -> Optional[str]:
        """
        V2 LLM summary: check cache first, generate if stale.

        Cache key in Blackboard: _chat_summary
        Staleness check: _chat_summary_count (message count when summary was made)
        """
        cached_summary = await self._blackboard.get_state(session_id, "_chat_summary")
        cached_count = await self._blackboard.get_state(session_id, "_chat_summary_count")

        if cached_summary and cached_count:
            try:
                cached_count = int(cached_count)
            except (ValueError, TypeError):
                cached_count = 0
            # Reuse cache if fewer than threshold new messages since last summary
            if total_count - cached_count < self._summary_threshold:
                return cached_summary

        # Generate new summary
        summary = await self._call_summary_llm(old_messages)
        if summary:
            await self._blackboard.set_state(session_id, "_chat_summary", summary)
            await self._blackboard.set_state(session_id, "_chat_summary_count", total_count)
        return summary

    async def _call_summary_llm(
        self,
        messages: List[ChatMessage],
    ) -> Optional[str]:
        """Call lightweight LLM to summarize conversation history."""
        conversation_text = self._format_for_summary(messages)

        llm_messages = [
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": conversation_text},
        ]

        def _sync_call() -> str:
            import dashscope

            response = dashscope.Generation.call(
                model=self._summary_model,
                messages=llm_messages,
                api_key=self._api_key,
                result_format="message",
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"Summary LLM error: {response.code} - {response.message}"
                )
            choice = response.output.choices[0].message
            return (choice.get("content", "") if isinstance(choice, dict) else getattr(choice, "content", "")) or ""

        try:
            result = await asyncio.to_thread(_sync_call)
            logger.info(
                "Generated chat summary (%d messages → %d chars)",
                len(messages), len(result or ""),
            )
            return result
        except Exception as e:
            logger.warning("Summary LLM call failed: %s — falling back to V1 trim", e)
            return None

    @staticmethod
    def _format_for_summary(messages: List[ChatMessage]) -> str:
        """Format messages into a readable text block for the summary LLM."""
        parts: List[str] = []
        for msg in messages:
            if msg.role == "user":
                parts.append(f"用户: {msg.content[:300]}")
            elif msg.role == "assistant" and msg.type == "text":
                parts.append(f"助手: {msg.content[:300]}")
            elif msg.role == "tool":
                parts.append(f"[工具 {msg.tool_name or ''}]: {msg.content[:150]}")
        return "\n".join(parts)
