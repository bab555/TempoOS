# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.
"""Unit tests for namespace helper."""

from tempo_os.kernel.namespace import get_key, get_channel


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
