# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Namespace Helper — Multi-tenancy key isolation.

All Redis keys are namespaced: tempo:{tenant_id}:{resource_type}:{resource_id}
This ensures complete data isolation between tenants.
"""

from __future__ import annotations


def get_key(tenant_id: str, resource_type: str, resource_id: str) -> str:
    """
    Build a tenant-scoped Redis key.

    Examples:
        get_key("t_001", "session", "abc") -> "tempo:t_001:session:abc"
        get_key("t_001", "artifact", "file_01") -> "tempo:t_001:artifact:file_01"
    """
    return f"tempo:{tenant_id}:{resource_type}:{resource_id}"


def get_chat_key(tenant_id: str, session_id: str) -> str:
    """
    Build a tenant-scoped chat history key (Redis List).

    Example:
        get_chat_key("t_001", "abc") -> "tempo:t_001:chat:abc"
    """
    return f"tempo:{tenant_id}:chat:{session_id}"


def get_results_key(tenant_id: str, session_id: str, tool_name: str) -> str:
    """
    Build a tenant-scoped accumulated tool results key (Redis List).

    Example:
        get_results_key("t_001", "abc", "search") -> "tempo:t_001:session:abc:results:search"
    """
    return f"tempo:{tenant_id}:session:{session_id}:results:{tool_name}"


def get_channel(tenant_id: str) -> str:
    """
    Build a tenant-scoped Pub/Sub channel name.

    Example:
        get_channel("t_001") -> "tempo:t_001:events"
    """
    return f"tempo:{tenant_id}:events"
