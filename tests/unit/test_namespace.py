# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for namespace helper."""

from tempo_os.kernel.namespace import get_key, get_channel, get_chat_key, get_results_key


class TestNamespace:
    def test_get_key(self):
        assert get_key("t_001", "session", "abc") == "tempo:t_001:session:abc"

    def test_get_key_artifact(self):
        assert get_key("t_001", "artifact", "file_01") == "tempo:t_001:artifact:file_01"

    def test_get_channel(self):
        assert get_channel("t_001") == "tempo:t_001:events"

    def test_different_tenants_different_keys(self):
        k1 = get_key("tenant_a", "session", "s1")
        k2 = get_key("tenant_b", "session", "s1")
        assert k1 != k2

    def test_different_tenants_different_channels(self):
        c1 = get_channel("tenant_a")
        c2 = get_channel("tenant_b")
        assert c1 != c2


class TestChatKey:
    def test_basic(self):
        assert get_chat_key("t_001", "abc") == "tempo:t_001:chat:abc"

    def test_tenant_isolation(self):
        k1 = get_chat_key("tenant_a", "s1")
        k2 = get_chat_key("tenant_b", "s1")
        assert k1 != k2

    def test_session_isolation(self):
        k1 = get_chat_key("t_001", "s1")
        k2 = get_chat_key("t_001", "s2")
        assert k1 != k2


class TestResultsKey:
    def test_basic(self):
        assert get_results_key("t_001", "abc", "search") == "tempo:t_001:session:abc:results:search"

    def test_different_tools(self):
        k1 = get_results_key("t_001", "s1", "search")
        k2 = get_results_key("t_001", "s1", "data_query")
        assert k1 != k2

    def test_tenant_isolation(self):
        k1 = get_results_key("tenant_a", "s1", "search")
        k2 = get_results_key("tenant_b", "s1", "search")
        assert k1 != k2
