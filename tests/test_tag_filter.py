"""Tests for tag filtering."""

from haproxy_azure_discovery.config import TagsConfig
from haproxy_azure_discovery.discovery.models import DiscoveredInstance
from haproxy_azure_discovery.discovery.tag_filter import TagFilter


def _inst(tags: dict[str, str], name: str = "vm1") -> DiscoveredInstance:
    return DiscoveredInstance(
        instance_id="id1",
        name=name,
        private_ip="10.0.0.1",
        service_name="app",
        service_port=80,
        region="eastus",
        resource_group="rg1",
        source="vm",
        tags=tags,
    )


class TestTagFilter:
    def test_no_filters_passes_all(self):
        filt = TagFilter(TagsConfig())
        instances = [_inst({"a": "1"}), _inst({"b": "2"})]
        assert len(filt.apply(instances)) == 2

    def test_allowlist_and_logic(self):
        filt = TagFilter(TagsConfig(allowlist={"env": "prod", "team": "infra"}))
        passes = _inst({"env": "prod", "team": "infra"})
        fails_one = _inst({"env": "prod", "team": "dev"})
        fails_both = _inst({"env": "staging"})
        result = filt.apply([passes, fails_one, fails_both])
        assert len(result) == 1
        assert result[0] is passes

    def test_denylist_or_logic(self):
        filt = TagFilter(TagsConfig(denylist={"skip": "true", "deprecated": "yes"}))
        denied_first = _inst({"skip": "true"})
        denied_second = _inst({"deprecated": "yes"})
        allowed = _inst({"skip": "false"})
        result = filt.apply([denied_first, denied_second, allowed])
        assert len(result) == 1
        assert result[0] is allowed

    def test_denylist_takes_precedence(self):
        filt = TagFilter(TagsConfig(
            allowlist={"env": "prod"},
            denylist={"skip": "true"},
        ))
        # Matches allowlist but also hits denylist
        both = _inst({"env": "prod", "skip": "true"})
        result = filt.apply([both])
        assert len(result) == 0
