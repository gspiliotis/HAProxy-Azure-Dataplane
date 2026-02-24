"""Tests for the AWS discovery client."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from haproxy_cloud_discovery.config import AWSConfig, TagsConfig
from haproxy_cloud_discovery.discovery.aws_client import AWSClient
from haproxy_cloud_discovery.discovery.models import DiscoveredInstance

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_TAGS_CONFIG = TagsConfig(
    service_name_tag="HAProxy:Service:Name",
    service_port_tag="HAProxy:Service:Port",
    instance_port_tag="HAProxy:Instance:Port",
)

DEFAULT_AWS_CONFIG = AWSConfig(region="us-east-1")


def _raw_instance(
    instance_id="i-abc123",
    private_ip="10.0.0.1",
    public_ip=None,
    az="us-east-1a",
    state="running",
    tags=None,
    launch_time=None,
) -> dict:
    """Build a minimal EC2 instance dict as returned by describe_instances."""
    default_tags = [
        {"Key": "HAProxy:Service:Name", "Value": "myapp"},
        {"Key": "HAProxy:Service:Port", "Value": "8080"},
        {"Key": "Name", "Value": f"web-{instance_id}"},
    ]
    result = {
        "InstanceId": instance_id,
        "PrivateIpAddress": private_ip,
        "State": {"Name": state},
        "Placement": {"AvailabilityZone": az},
        "Tags": tags if tags is not None else default_tags,
        "LaunchTime": launch_time or datetime(2024, 1, 1, tzinfo=timezone.utc),
        "OwnerId": "123456789012",
    }
    if public_ip:
        result["PublicIpAddress"] = public_ip
    return result


def _describe_instances_response(*instances) -> dict:
    return {
        "Reservations": [
            {"Instances": list(instances)},
        ]
    }


def _make_paginator_response(page_data: list[dict]):
    """Create a mock paginator that yields the given pages."""
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = iter(page_data)
    return mock_paginator


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAWSClientEC2Discovery:
    """Tests for _discover_ec2()."""

    def _make_client(self, ec2_mock, asg_mock=None) -> AWSClient:
        with patch("boto3.Session") as MockSession:
            session = MagicMock()
            MockSession.return_value = session
            session.client.side_effect = lambda svc, **kw: (
                ec2_mock if svc == "ec2" else (asg_mock or MagicMock())
            )
            client = AWSClient(DEFAULT_AWS_CONFIG, DEFAULT_TAGS_CONFIG)
        client._ec2 = ec2_mock
        client._autoscaling = asg_mock or MagicMock()
        return client

    def test_discovers_running_ec2_instance(self):
        ec2 = MagicMock()
        raw = _raw_instance()
        ec2.get_paginator.return_value = _make_paginator_response(
            [_describe_instances_response(raw)]
        )
        # Empty ASG response
        asg = MagicMock()
        asg.get_paginator.return_value = _make_paginator_response([{"AutoScalingGroups": []}])

        client = self._make_client(ec2, asg)
        instances = client.discover_all()

        assert len(instances) == 1
        inst = instances[0]
        assert inst.instance_id == "i-abc123"
        assert inst.private_ip == "10.0.0.1"
        assert inst.service_name == "myapp"
        assert inst.service_port == 8080
        assert inst.source == "ec2"
        assert inst.availability_zone == "us-east-1a"
        assert inst.region == "us-east-1"  # AZ without trailing letter
        assert inst.power_state == "running"

    def test_instance_name_from_name_tag(self):
        ec2 = MagicMock()
        raw = _raw_instance(instance_id="i-001", tags=[
            {"Key": "HAProxy:Service:Name", "Value": "api"},
            {"Key": "HAProxy:Service:Port", "Value": "443"},
            {"Key": "Name", "Value": "api-server-1"},
        ])
        ec2.get_paginator.return_value = _make_paginator_response(
            [_describe_instances_response(raw)]
        )
        asg = MagicMock()
        asg.get_paginator.return_value = _make_paginator_response([{"AutoScalingGroups": []}])

        client = self._make_client(ec2, asg)
        inst = client.discover_all()[0]
        assert inst.name == "api-server-1"
        assert inst.service_name == "api"

    def test_instance_without_service_name_tag_skipped(self):
        ec2 = MagicMock()
        raw = _raw_instance(tags=[
            {"Key": "HAProxy:Service:Port", "Value": "8080"},
        ])
        ec2.get_paginator.return_value = _make_paginator_response(
            [_describe_instances_response(raw)]
        )
        asg = MagicMock()
        asg.get_paginator.return_value = _make_paginator_response([{"AutoScalingGroups": []}])

        client = self._make_client(ec2, asg)
        assert client.discover_all() == []

    def test_instance_without_service_port_tag_skipped(self):
        ec2 = MagicMock()
        raw = _raw_instance(tags=[
            {"Key": "HAProxy:Service:Name", "Value": "app"},
        ])
        ec2.get_paginator.return_value = _make_paginator_response(
            [_describe_instances_response(raw)]
        )
        asg = MagicMock()
        asg.get_paginator.return_value = _make_paginator_response([{"AutoScalingGroups": []}])

        client = self._make_client(ec2, asg)
        assert client.discover_all() == []

    def test_instance_without_private_ip_skipped(self):
        ec2 = MagicMock()
        raw = _raw_instance()
        del raw["PrivateIpAddress"]
        ec2.get_paginator.return_value = _make_paginator_response(
            [_describe_instances_response(raw)]
        )
        asg = MagicMock()
        asg.get_paginator.return_value = _make_paginator_response([{"AutoScalingGroups": []}])

        client = self._make_client(ec2, asg)
        assert client.discover_all() == []

    def test_non_integer_port_tag_skipped(self):
        ec2 = MagicMock()
        raw = _raw_instance(tags=[
            {"Key": "HAProxy:Service:Name", "Value": "app"},
            {"Key": "HAProxy:Service:Port", "Value": "notaport"},
        ])
        ec2.get_paginator.return_value = _make_paginator_response(
            [_describe_instances_response(raw)]
        )
        asg = MagicMock()
        asg.get_paginator.return_value = _make_paginator_response([{"AutoScalingGroups": []}])

        client = self._make_client(ec2, asg)
        assert client.discover_all() == []

    def test_instance_port_override(self):
        ec2 = MagicMock()
        raw = _raw_instance(tags=[
            {"Key": "HAProxy:Service:Name", "Value": "app"},
            {"Key": "HAProxy:Service:Port", "Value": "8080"},
            {"Key": "HAProxy:Instance:Port", "Value": "9090"},
        ])
        ec2.get_paginator.return_value = _make_paginator_response(
            [_describe_instances_response(raw)]
        )
        asg = MagicMock()
        asg.get_paginator.return_value = _make_paginator_response([{"AutoScalingGroups": []}])

        client = self._make_client(ec2, asg)
        inst = client.discover_all()[0]
        assert inst.instance_port == 9090
        assert inst.effective_port == 9090

    def test_public_ip_captured(self):
        ec2 = MagicMock()
        raw = _raw_instance(public_ip="52.0.0.1")
        ec2.get_paginator.return_value = _make_paginator_response(
            [_describe_instances_response(raw)]
        )
        asg = MagicMock()
        asg.get_paginator.return_value = _make_paginator_response([{"AutoScalingGroups": []}])

        client = self._make_client(ec2, asg)
        inst = client.discover_all()[0]
        assert inst.public_ip == "52.0.0.1"

    def test_multiple_instances_across_pages(self):
        ec2 = MagicMock()
        page1 = _describe_instances_response(_raw_instance("i-001"))
        page2 = _describe_instances_response(_raw_instance("i-002"))
        ec2.get_paginator.return_value = _make_paginator_response([page1, page2])
        asg = MagicMock()
        asg.get_paginator.return_value = _make_paginator_response([{"AutoScalingGroups": []}])

        client = self._make_client(ec2, asg)
        assert len(client.discover_all()) == 2

    def test_az_string_stored_verbatim(self):
        """Full AWS AZ name should be stored as-is in availability_zone."""
        ec2 = MagicMock()
        raw = _raw_instance(az="us-west-2b")
        ec2.get_paginator.return_value = _make_paginator_response(
            [_describe_instances_response(raw)]
        )
        asg = MagicMock()
        asg.get_paginator.return_value = _make_paginator_response([{"AutoScalingGroups": []}])

        client = self._make_client(ec2, asg)
        inst = client.discover_all()[0]
        assert inst.availability_zone == "us-west-2b"
        assert inst.region == "us-west-2"  # letter stripped for region


class TestAWSClientASGDiscovery:
    """Tests for _discover_asg()."""

    def _make_client(self, ec2_mock, asg_mock) -> AWSClient:
        client = AWSClient.__new__(AWSClient)
        client._config = DEFAULT_AWS_CONFIG
        client._tags = DEFAULT_TAGS_CONFIG
        client._ec2 = ec2_mock
        client._autoscaling = asg_mock
        return client

    def test_discovers_asg_instances(self):
        asg = MagicMock()
        asg.get_paginator.return_value = _make_paginator_response([{
            "AutoScalingGroups": [{
                "AutoScalingGroupName": "my-asg",
                "Instances": [
                    {"InstanceId": "i-asg1"},
                    {"InstanceId": "i-asg2"},
                ],
            }]
        }])

        ec2 = MagicMock()
        # EC2 returns paginator with empty response (no direct EC2 instances)
        ec2.get_paginator.return_value = _make_paginator_response([{"Reservations": []}])
        # describe_instances for ASG resolution
        ec2.describe_instances.return_value = _describe_instances_response(
            _raw_instance("i-asg1", private_ip="10.0.1.1"),
            _raw_instance("i-asg2", private_ip="10.0.1.2"),
        )

        client = self._make_client(ec2, asg)
        instances = client.discover_all()

        assert len(instances) == 2
        assert all(inst.source == "asg" for inst in instances)
        ips = {inst.private_ip for inst in instances}
        assert ips == {"10.0.1.1", "10.0.1.2"}

    def test_asg_deduplicates_with_ec2(self):
        """ASG members already discovered via EC2 should not be duplicated."""
        # EC2 paginator finds i-shared
        ec2 = MagicMock()
        ec2.get_paginator.return_value = _make_paginator_response([
            _describe_instances_response(_raw_instance("i-shared", private_ip="10.0.0.1"))
        ])

        # ASG also contains i-shared plus a new instance
        asg = MagicMock()
        asg.get_paginator.return_value = _make_paginator_response([{
            "AutoScalingGroups": [{
                "AutoScalingGroupName": "my-asg",
                "Instances": [
                    {"InstanceId": "i-shared"},
                    {"InstanceId": "i-new"},
                ],
            }]
        }])
        # ASG resolution only resolves i-new (i-shared is excluded)
        ec2.describe_instances.return_value = _describe_instances_response(
            _raw_instance("i-new", private_ip="10.0.0.2"),
        )

        client = self._make_client(ec2, asg)
        instances = client.discover_all()

        ids = {inst.instance_id for inst in instances}
        assert ids == {"i-shared", "i-new"}
        assert len(instances) == 2

    def test_empty_asg_skips_ec2_describe(self):
        """When no ASG instances are found, describe_instances is not called."""
        ec2 = MagicMock()
        ec2.get_paginator.return_value = _make_paginator_response([{"Reservations": []}])

        asg = MagicMock()
        asg.get_paginator.return_value = _make_paginator_response([{"AutoScalingGroups": []}])

        client = self._make_client(ec2, asg)
        client.discover_all()
        ec2.describe_instances.assert_not_called()


class TestAWSClientCredentials:
    """Tests for credential and session configuration."""

    def test_default_credential_chain(self):
        """No credential_profile means session is created without profile_name."""
        with patch("boto3.Session") as MockSession:
            session = MagicMock()
            MockSession.return_value = session
            session.client.return_value = MagicMock()
            AWSClient(AWSConfig(region="us-east-1"), DEFAULT_TAGS_CONFIG)
            MockSession.assert_called_once_with(region_name="us-east-1")

    def test_named_profile(self):
        """credential_profile should be passed as profile_name to boto3.Session."""
        with patch("boto3.Session") as MockSession:
            session = MagicMock()
            MockSession.return_value = session
            session.client.return_value = MagicMock()
            AWSClient(
                AWSConfig(region="us-east-1", credential_profile="my-profile"),
                DEFAULT_TAGS_CONFIG,
            )
            MockSession.assert_called_once_with(
                region_name="us-east-1", profile_name="my-profile"
            )
