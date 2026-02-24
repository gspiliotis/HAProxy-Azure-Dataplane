"""AWS boto3 client for discovering EC2 instances and Auto Scaling Group members."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import boto3

from ..config import AWSConfig, TagsConfig
from .models import DiscoveredInstance

logger = logging.getLogger(__name__)


class AWSClient:
    """Discovers EC2 instances and ASG members tagged for HAProxy service discovery."""

    def __init__(self, aws_config: AWSConfig, tags_config: TagsConfig):
        self._config = aws_config
        self._tags = tags_config

        session_kwargs: dict[str, Any] = {"region_name": aws_config.region}
        if aws_config.credential_profile:
            session_kwargs["profile_name"] = aws_config.credential_profile

        session = boto3.Session(**session_kwargs)
        self._ec2 = session.client("ec2")
        self._autoscaling = session.client("autoscaling")

    def discover_all(self) -> list[DiscoveredInstance]:
        """Run full discovery: EC2 + ASG instances. Returns only running instances with required tags."""
        ec2_instances = self._discover_ec2()
        asg_instances = self._discover_asg(known_ids={i.instance_id for i in ec2_instances})
        instances = ec2_instances + asg_instances
        logger.info("Discovery complete", extra={"total_instances": len(instances)})
        return instances

    # ── EC2 discovery ────────────────────────────────────────────────

    def _discover_ec2(self) -> list[DiscoveredInstance]:
        """Enumerate EC2 instances tagged with HAProxy:Service:Name."""
        instances: list[DiscoveredInstance] = []

        paginator = self._ec2.get_paginator("describe_instances")
        pages = paginator.paginate(
            Filters=[
                {"Name": f"tag-key", "Values": [self._tags.service_name_tag]},
                {"Name": "instance-state-name", "Values": ["running"]},
            ]
        )

        for page in pages:
            for reservation in page.get("Reservations", []):
                for raw in reservation.get("Instances", []):
                    inst = self._parse_ec2_instance(raw, source="ec2")
                    if inst is not None:
                        instances.append(inst)

        logger.info("EC2 discovery found %d instances", len(instances))
        return instances

    # ── ASG discovery ────────────────────────────────────────────────

    def _discover_asg(self, known_ids: set[str]) -> list[DiscoveredInstance]:
        """Enumerate instances in Auto Scaling Groups tagged with HAProxy:Service:Name.

        Instances already discovered via EC2 (known_ids) are skipped to avoid duplicates.
        """
        asg_instance_ids: list[str] = []

        paginator = self._autoscaling.get_paginator("describe_auto_scaling_groups")
        pages = paginator.paginate(
            Filters=[{"Name": "tag-key", "Values": [self._tags.service_name_tag]}]
        )

        for page in pages:
            for asg in page.get("AutoScalingGroups", []):
                for member in asg.get("Instances", []):
                    iid = member.get("InstanceId", "")
                    if iid and iid not in known_ids:
                        asg_instance_ids.append(iid)

        if not asg_instance_ids:
            logger.info("ASG discovery found 0 instances")
            return []

        # Resolve IPs and tags via EC2 describe_instances
        instances: list[DiscoveredInstance] = []
        for chunk in _chunks(asg_instance_ids, 100):
            response = self._ec2.describe_instances(
                InstanceIds=chunk,
                Filters=[{"Name": "instance-state-name", "Values": ["running"]}],
            )
            for reservation in response.get("Reservations", []):
                for raw in reservation.get("Instances", []):
                    inst = self._parse_ec2_instance(raw, source="asg")
                    if inst is not None:
                        instances.append(inst)

        logger.info("ASG discovery found %d instances", len(instances))
        return instances

    # ── Shared parsing ────────────────────────────────────────────────

    def _parse_ec2_instance(self, raw: dict[str, Any], source: str) -> DiscoveredInstance | None:
        """Parse a raw EC2 instance dict into a DiscoveredInstance.

        Returns None if required tags are missing or private IP is absent.
        """
        tags = {t["Key"]: t["Value"] for t in raw.get("Tags", [])}

        service_name = tags.get(self._tags.service_name_tag)
        service_port_str = tags.get(self._tags.service_port_tag)
        if not service_name or not service_port_str:
            return None

        try:
            service_port = int(service_port_str)
        except ValueError:
            logger.warning(
                "EC2 instance %s has non-integer service port tag: %s",
                raw.get("InstanceId"), service_port_str,
            )
            return None

        private_ip = raw.get("PrivateIpAddress")
        if not private_ip:
            logger.warning("EC2 instance %s has no private IP, skipping", raw.get("InstanceId"))
            return None

        instance_port = self._parse_instance_port(tags)
        public_ip = raw.get("PublicIpAddress")

        # Availability zone — full AZ name, e.g. "us-east-1a"
        placement = raw.get("Placement", {})
        availability_zone: str | None = placement.get("AvailabilityZone") or None

        # Region is the AZ string minus the trailing letter
        region = availability_zone[:-1] if availability_zone else self._config.region

        launch_time: datetime | None = raw.get("LaunchTime")
        if isinstance(launch_time, datetime) and launch_time.tzinfo is None:
            launch_time = launch_time.replace(tzinfo=timezone.utc)

        account_id = self._config.account_id or raw.get("OwnerId", "")

        return DiscoveredInstance(
            instance_id=raw["InstanceId"],
            name=tags.get("Name", raw["InstanceId"]),
            private_ip=private_ip,
            service_name=service_name,
            service_port=service_port,
            instance_port=instance_port,
            region=region,
            namespace=account_id,
            source=source,
            tags=tags,
            public_ip=public_ip,
            availability_zone=availability_zone,
            created_at=launch_time,
            power_state="running",
        )

    def _parse_instance_port(self, tags: dict[str, str]) -> int | None:
        """Parse the optional HAProxy:Instance:Port tag."""
        raw = tags.get(self._tags.instance_port_tag)
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None


def _chunks(lst: list, size: int):
    """Yield successive fixed-size chunks from lst."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]
