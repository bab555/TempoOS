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


def get_channel(tenant_id: str) -> str:
    """
    Build a tenant-scoped Pub/Sub channel name.

    Example:
        get_channel("t_001") -> "tempo:t_001:events"
    """
    return f"tempo:{tenant_id}:events"
