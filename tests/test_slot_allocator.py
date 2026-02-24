"""Tests for the slot allocator."""

from haproxy_cloud_discovery.config import ServerSlotsConfig
from haproxy_cloud_discovery.haproxy.slot_allocator import SlotAllocator


class TestSlotAllocator:
    def test_returns_base_when_count_is_below(self):
        alloc = SlotAllocator(ServerSlotsConfig(base=10))
        assert alloc.calculate_slots(5) == 10

    def test_returns_base_when_count_equals_base(self):
        alloc = SlotAllocator(ServerSlotsConfig(base=10))
        assert alloc.calculate_slots(10) == 10

    def test_linear_growth(self):
        alloc = SlotAllocator(ServerSlotsConfig(base=10, growth_factor=1.5, growth_type="linear"))
        # 15 active: extra = ceil((15-10) * 1.5) = ceil(7.5) = 8; total = 10 + 8 = 18
        result = alloc.calculate_slots(15)
        assert result == 18

    def test_exponential_growth(self):
        alloc = SlotAllocator(ServerSlotsConfig(base=10, growth_factor=2.0, growth_type="exponential"))
        # 15 active: 10 * 2^1 = 20 >= 15, so 20
        result = alloc.calculate_slots(15)
        assert result == 20

    def test_exponential_growth_larger(self):
        alloc = SlotAllocator(ServerSlotsConfig(base=10, growth_factor=2.0, growth_type="exponential"))
        # 25 active: 10 * 2^1 = 20 < 25, 10 * 2^2 = 40 >= 25
        result = alloc.calculate_slots(25)
        assert result == 40

    def test_zero_count(self):
        alloc = SlotAllocator(ServerSlotsConfig(base=10))
        assert alloc.calculate_slots(0) == 10

    def test_generate_server_names(self):
        names = SlotAllocator.generate_server_names(3)
        assert names == ["srv1", "srv2", "srv3"]

    def test_generate_zero_names(self):
        assert SlotAllocator.generate_server_names(0) == []
