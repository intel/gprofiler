#
# Copyright (C) 2025 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)


class CgroupVersion(Enum):
    """Cgroup version enumeration"""

    V1 = "v1"
    V2 = "v2"
    UNKNOWN = "unknown"


@dataclass
class CgroupResourceUsage:
    """Represents resource usage for a cgroup"""

    cgroup_path: str
    name: str
    cpu_usage: int  # CPU usage in nanoseconds
    memory_usage: int  # Memory usage in bytes

    @property
    def total_score(self) -> float:
        """Calculate a combined score for ranking cgroups by resource usage

        Prioritizes CPU usage over memory since CPU indicates active processes
        that are more interesting for profiling.
        """
        # Normalize CPU (ns) and memory (bytes) to comparable scales
        cpu_score = self.cpu_usage / 1_000_000_000  # ns to seconds
        memory_score = self.memory_usage / (1024 * 1024)  # bytes to MB

        # Weight CPU heavily (10x) since active CPU usage is more important for profiling
        # than static memory usage
        return (cpu_score * 10) + memory_score


def detect_cgroup_version() -> CgroupVersion:
    """Detect which cgroup version is in use for Docker containers"""
    try:
        # Check if Docker containers are using cgroup v1 paths (hybrid systems)
        if os.path.exists("/sys/fs/cgroup/memory/docker") or os.path.exists("/sys/fs/cgroup/cpu,cpuacct/docker"):
            return CgroupVersion.V1

        # Check if cgroup v2 is mounted and being used
        with open("/proc/mounts", "r") as f:
            mounts = f.read()
            if "cgroup2" in mounts and "/sys/fs/cgroup" in mounts:
                # Check if Docker containers exist in v2 paths
                v2_docker_paths = ["/sys/fs/cgroup/system.slice", "/sys/fs/cgroup/docker"]
                for path in v2_docker_paths:
                    if os.path.exists(path):
                        try:
                            entries = os.listdir(path)
                            if any("docker" in entry.lower() for entry in entries):
                                return CgroupVersion.V2
                        except (OSError, PermissionError):
                            continue

                # If cgroup2 is mounted but no Docker containers found in v2, check v1
                if "/sys/fs/cgroup/memory" in mounts or "/sys/fs/cgroup/cpu" in mounts:
                    return CgroupVersion.V1
                else:
                    return CgroupVersion.V2
            elif "cgroup" in mounts and ("/sys/fs/cgroup/memory" in mounts or "/sys/fs/cgroup/cpu" in mounts):
                return CgroupVersion.V1
    except (IOError, OSError) as e:
        logger.debug(f"Failed to read /proc/mounts: {e}")

    # Fallback: check filesystem structure
    if os.path.exists("/sys/fs/cgroup/memory") or os.path.exists("/sys/fs/cgroup/cpu,cpuacct"):
        return CgroupVersion.V1
    elif os.path.exists("/sys/fs/cgroup/cgroup.controllers"):
        return CgroupVersion.V2

    return CgroupVersion.UNKNOWN


def is_cgroup_available() -> bool:
    """Check if cgroup filesystem is available and mounted"""
    return os.path.exists("/sys/fs/cgroup") and detect_cgroup_version() != CgroupVersion.UNKNOWN


def get_cgroup_cpu_usage(cgroup_path: str) -> Optional[int]:
    """Get CPU usage for a cgroup in nanoseconds"""
    cgroup_version = detect_cgroup_version()

    if cgroup_version == CgroupVersion.V2:
        # cgroup v2 uses cpu.stat file
        cpu_stat_file = os.path.join(cgroup_path, "cpu.stat")
        if os.path.exists(cpu_stat_file):
            try:
                with open(cpu_stat_file, "r") as f:
                    for line in f:
                        if line.startswith("usage_usec "):
                            # Convert microseconds to nanoseconds
                            return int(line.split()[1]) * 1000
            except (IOError, ValueError) as e:
                logger.debug(f"Failed to read CPU usage from {cpu_stat_file}: {e}")
        return None

    else:  # cgroup v1
        usage_file = os.path.join(cgroup_path, "cpuacct.usage")
        if not os.path.exists(usage_file):
            # Try alternative path
            alt_path = cgroup_path.replace("/cpu,cpuacct/", "/cpuacct/")
            usage_file = os.path.join(alt_path, "cpuacct.usage")
            if not os.path.exists(usage_file):
                return None

        try:
            with open(usage_file, "r") as f:
                return int(f.read().strip())
        except (IOError, ValueError) as e:
            logger.debug(f"Failed to read CPU usage from {usage_file}: {e}")
            return None


def get_cgroup_memory_usage(cgroup_path: str) -> Optional[int]:
    """Get memory usage for a cgroup in bytes"""
    cgroup_version = detect_cgroup_version()

    if cgroup_version == CgroupVersion.V2:
        # cgroup v2 uses memory.current file
        usage_file = os.path.join(cgroup_path, "memory.current")
    else:  # cgroup v1
        usage_file = os.path.join(cgroup_path, "memory.usage_in_bytes")

    if not os.path.exists(usage_file):
        return None

    try:
        with open(usage_file, "r") as f:
            return int(f.read().strip())
    except (IOError, ValueError) as e:
        logger.debug(f"Failed to read memory usage from {usage_file}: {e}")
        return None


def find_all_cgroups() -> List[str]:
    """Find all available cgroups in the system"""
    cgroups = []
    cgroup_version = detect_cgroup_version()

    if cgroup_version == CgroupVersion.V2:
        # cgroup v2 unified hierarchy
        base = "/sys/fs/cgroup"
        try:
            for root, dirs, files in os.walk(base):
                # Skip the root directory itself
                if root == base:
                    continue

                # Check if this directory has the necessary files for v2
                cpu_file = os.path.join(root, "cpu.stat")
                memory_file = os.path.join(root, "memory.current")

                if os.path.exists(cpu_file) or os.path.exists(memory_file):
                    cgroups.append(root)
        except OSError as e:
            logger.debug(f"Error walking cgroup v2 directory {base}: {e}")

    else:  # cgroup v1
        # Common cgroup mount points to check
        cgroup_bases = [
            "/sys/fs/cgroup/cpu,cpuacct",
            "/sys/fs/cgroup/memory",
            "/sys/fs/cgroup/cpuacct",
        ]

        for base in cgroup_bases:
            if os.path.exists(base):
                try:
                    # Walk through all subdirectories
                    for root, dirs, files in os.walk(base):
                        # Skip the base directory itself
                        if root == base:
                            continue

                        # Check if this directory has the necessary files
                        cpu_file = os.path.join(root, "cpuacct.usage")
                        memory_file = root.replace("/cpu,cpuacct/", "/memory/") + "/memory.usage_in_bytes"

                        if os.path.exists(cpu_file) or os.path.exists(memory_file):
                            cgroups.append(root)
                except OSError as e:
                    logger.debug(f"Error walking cgroup directory {base}: {e}")
                    continue

    return list(set(cgroups))  # Remove duplicates


def get_cgroup_resource_usage(cgroup_path: str) -> Optional[CgroupResourceUsage]:
    """Get resource usage for a single cgroup"""
    cpu_usage = get_cgroup_cpu_usage(cgroup_path)

    # For memory, try to find the corresponding memory cgroup path
    memory_path = cgroup_path.replace("/cpu,cpuacct/", "/memory/")
    if not os.path.exists(memory_path):
        memory_path = cgroup_path.replace("/cpuacct/", "/memory/")

    memory_usage = get_cgroup_memory_usage(memory_path)

    # If we can't get any usage data, skip this cgroup
    if cpu_usage is None and memory_usage is None:
        return None

    # Use 0 as default if one metric is missing
    cpu_usage = cpu_usage or 0
    memory_usage = memory_usage or 0

    # Extract a readable name from the path
    name = os.path.basename(cgroup_path)
    if len(name) > 12:  # Truncate long container IDs
        name = name[:12]

    return CgroupResourceUsage(cgroup_path=cgroup_path, name=name, cpu_usage=cpu_usage, memory_usage=memory_usage)


def get_top_cgroups_by_usage(limit: int = 50) -> List[CgroupResourceUsage]:
    """Get the top N cgroups by resource usage"""
    if not is_cgroup_available():
        logger.warning("Cgroup filesystem not available")
        return []

    all_cgroups = find_all_cgroups()
    logger.debug(f"Found {len(all_cgroups)} cgroups to analyze")

    cgroup_usages = []
    for cgroup_path in all_cgroups:
        usage = get_cgroup_resource_usage(cgroup_path)
        if usage:
            cgroup_usages.append(usage)

    # Sort by total resource usage score (descending)
    cgroup_usages.sort(key=lambda x: x.total_score, reverse=True)

    logger.debug(f"Analyzed {len(cgroup_usages)} cgroups with resource data")

    return cgroup_usages[:limit]


def cgroup_to_perf_name(cgroup_path: str) -> str:
    """Convert a cgroup path to the name format expected by perf -G option"""
    # perf expects the cgroup name relative to the cgroup mount point
    # For example: /sys/fs/cgroup/memory/docker/abc123 -> docker/abc123

    # Find the relative path from the cgroup mount point
    for base in ["/sys/fs/cgroup/memory/", "/sys/fs/cgroup/cpu,cpuacct/", "/sys/fs/cgroup/cpuacct/"]:
        if cgroup_path.startswith(base):
            return cgroup_path[len(base) :]

    # Fallback: just use the basename
    return os.path.basename(cgroup_path)


def convert_cgroupv2_path_to_perf_name(cgroup_path: str) -> str:
    """Convert a cgroup v2 path to perf-compatible name"""
    # Remove the base cgroup path
    if cgroup_path.startswith("/sys/fs/cgroup/"):
        relative_path = cgroup_path[len("/sys/fs/cgroup/") :]
    else:
        relative_path = cgroup_path

    # Handle Docker container paths in cgroup v2
    if "docker-" in relative_path and ".scope" in relative_path:
        # Extract container ID from system.slice/docker-<container_id>.scope
        import re

        match = re.search(r"docker-([a-f0-9]{64})\.scope", relative_path)
        if match:
            container_id = match.group(1)
            return f"docker/{container_id}"

    # Handle other Docker paths
    if relative_path.startswith("docker/"):
        return relative_path

    # For other cgroups, use the relative path
    return relative_path


def validate_cgroup_perf_event_access(cgroup_name: str) -> bool:
    """Check if a cgroup is available for perf profiling"""
    cgroup_version = detect_cgroup_version()

    if cgroup_version == CgroupVersion.V2:
        # In cgroup v2, perf events are handled differently
        # The cgroup path should exist in the unified hierarchy
        if cgroup_name.startswith("docker/"):
            # For Docker containers in cgroup v2, check common paths
            container_id = cgroup_name.replace("docker/", "")
            possible_paths = [
                f"/sys/fs/cgroup/system.slice/docker-{container_id}.scope",
                f"/sys/fs/cgroup/docker/{container_id}",
                f"/sys/fs/cgroup/system.slice/docker.service/docker/{container_id}",
            ]
            for path in possible_paths:
                if os.path.exists(path) and os.path.isdir(path):
                    return True
            return False
        else:
            # For other cgroups in v2, check if the path exists
            # Handle both absolute and relative paths
            if cgroup_name.startswith("/sys/fs/cgroup/"):
                cgroup_path = cgroup_name
            else:
                cgroup_path = f"/sys/fs/cgroup/{cgroup_name}"
            return os.path.exists(cgroup_path) and os.path.isdir(cgroup_path)

    else:  # cgroup v1
        perf_event_path = f"/sys/fs/cgroup/perf_event/{cgroup_name}"
        return os.path.exists(perf_event_path) and os.path.isdir(perf_event_path)


def get_top_docker_containers_for_perf(limit: int) -> List[str]:
    """Get top Docker containers by resource usage for perf profiling

    Returns individual Docker container cgroup names that exist in perf_event controller.
    """
    import subprocess

    docker_containers = []
    cgroup_version = detect_cgroup_version()

    try:
        # Get running Docker containers with resource stats
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            container_stats = []
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        container_id = parts[0]
                        cpu_percent_str = parts[1].replace("%", "")
                        try:
                            cpu_percent = float(cpu_percent_str)
                            container_stats.append((container_id, cpu_percent))
                        except ValueError:
                            continue

            # Sort by CPU usage (descending)
            container_stats.sort(key=lambda x: x[1], reverse=True)

            # Get full container IDs and check perf_event access
            for container_id, cpu_percent in container_stats[
                : limit * 2
            ]:  # Get more than needed in case some don't have perf access
                try:
                    # Get full container ID
                    full_id_result = subprocess.run(
                        ["docker", "inspect", "--format", "{{.Id}}", container_id],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )

                    if full_id_result.returncode == 0:
                        full_id = full_id_result.stdout.strip()

                        if cgroup_version == CgroupVersion.V2:
                            # For cgroup v2, we need to find the actual cgroup path
                            # and use the relative path for perf
                            possible_paths = [
                                f"/sys/fs/cgroup/system.slice/docker-{full_id}.scope",
                                f"/sys/fs/cgroup/docker/{full_id}",
                                f"/sys/fs/cgroup/system.slice/docker.service/docker/{full_id}",
                            ]

                            docker_cgroup = None
                            for path in possible_paths:
                                if os.path.exists(path) and os.path.isdir(path):
                                    # For cgroup v2, perf expects the relative path from /sys/fs/cgroup/
                                    docker_cgroup = path.replace("/sys/fs/cgroup/", "")
                                    logger.debug(
                                        f"Found cgroup v2 path for container {container_id}: {path} -> {docker_cgroup}"
                                    )
                                    break

                            if not docker_cgroup:
                                # Fallback: try to find any docker-related path for this container
                                try:
                                    import glob

                                    pattern = f"/sys/fs/cgroup/**/docker*{full_id[:12]}*"
                                    matches = glob.glob(pattern, recursive=True)
                                    if matches:
                                        docker_cgroup = matches[0].replace("/sys/fs/cgroup/", "")
                                        logger.debug(f"Found fallback cgroup v2 path: {matches[0]} -> {docker_cgroup}")
                                    else:
                                        docker_cgroup = f"docker/{full_id}"  # Last resort fallback
                                        logger.debug(f"No cgroup v2 path found, using fallback: {docker_cgroup}")
                                except Exception as e:
                                    docker_cgroup = f"docker/{full_id}"
                                    logger.debug(f"Error finding cgroup v2 path: {e}, using fallback: {docker_cgroup}")
                        else:
                            # cgroup v1 format
                            docker_cgroup = f"docker/{full_id}"

                        # Check if this container has perf_event access
                        if validate_cgroup_perf_event_access(docker_cgroup):
                            docker_containers.append(docker_cgroup)
                            logger.debug(
                                f"Added Docker container for profiling: {container_id} "
                                f"(CPU: {cpu_percent}%) -> {docker_cgroup}"
                            )

                            if len(docker_containers) >= limit:
                                break
                        else:
                            logger.debug(f"Docker container {container_id} not available for perf profiling")

                except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
                    logger.debug(f"Failed to get full ID for container {container_id}: {e}")
                    continue

    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.debug(f"Failed to get Docker container stats: {e}")

    return docker_containers


def get_top_cgroup_names_for_perf(limit: int = 50, max_docker_containers: int = 0) -> List[str]:
    """Get top cgroup names in the format needed for perf -G option

    Args:
        limit: Maximum total number of cgroups to return
        max_docker_containers: If > 0, profile individual Docker containers instead of broad 'docker' cgroup

    Only returns cgroups that exist in both resource controllers (memory/cpu)
    and the perf_event controller, since perf needs access to both.
    """
    if max_docker_containers > 0:
        # Use individual Docker container profiling
        docker_containers = get_top_docker_containers_for_perf(max_docker_containers)

        # Get other non-Docker cgroups
        top_cgroups = get_top_cgroups_by_usage(limit)
        other_cgroups = []
        seen_names = set(docker_containers)  # Track unique cgroup names to avoid duplicates

        for cgroup in top_cgroups:
            cgroup_name = cgroup_to_perf_name(cgroup.cgroup_path)

            # Skip Docker cgroups (we're handling them individually)
            if cgroup_name.startswith("docker"):
                continue

            # Skip duplicates
            if cgroup_name in seen_names:
                logger.debug(f"Skipping duplicate cgroup name {cgroup_name}")
                continue

            if validate_cgroup_perf_event_access(cgroup_name):
                other_cgroups.append(cgroup_name)
                seen_names.add(cgroup_name)

                # Respect total limit
                if len(docker_containers) + len(other_cgroups) >= limit:
                    break
            else:
                logger.debug(f"Skipping cgroup {cgroup_name} - not available in perf_event controller")

        valid_cgroups = docker_containers + other_cgroups

        if docker_containers:
            logger.info(
                f"Using individual Docker container profiling: {len(docker_containers)} containers, "
                f"{len(other_cgroups)} other cgroups"
            )

    else:
        # Use traditional cgroup profiling (including broad 'docker' cgroup)
        top_cgroups = get_top_cgroups_by_usage(limit)
        valid_cgroups = []
        seen_names = set()  # Track unique cgroup names to avoid duplicates

        for cgroup in top_cgroups:
            cgroup_name = cgroup_to_perf_name(cgroup.cgroup_path)

            # Skip duplicates (same cgroup from different controllers)
            if cgroup_name in seen_names:
                logger.debug(f"Skipping duplicate cgroup name {cgroup_name}")
                continue

            if validate_cgroup_perf_event_access(cgroup_name):
                valid_cgroups.append(cgroup_name)
                seen_names.add(cgroup_name)
            else:
                logger.debug(f"Skipping cgroup {cgroup_name} - not available in perf_event controller")

    if len(valid_cgroups) < limit:
        logger.info(f"Filtered cgroups for perf: {len(valid_cgroups)}/{limit} cgroups have perf_event access")

    return valid_cgroups


def validate_perf_cgroup_support() -> bool:
    """Check if the current perf binary supports cgroup filtering"""
    try:
        import subprocess

        result = subprocess.run(["perf", "record", "--help"], capture_output=True, text=True, timeout=10)
        return "--cgroup" in result.stdout or "-G" in result.stdout
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        return False
