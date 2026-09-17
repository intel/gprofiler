#
# Copyright (C) 2026 Intel Corporation
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

"""
PMU (Performance Monitoring Unit) event constants and detection.

This module provides:
- Hardware PMU event constants
- Event detection via 'perf list pmu'
- Event validation and normalization
"""

import logging
import subprocess
from typing import List, Set

logger = logging.getLogger(__name__)

# =============================================================================
# GLOBAL CONSTANTS
# =============================================================================

# Hardware PMU events that gprofiler supports
# These are standard perf hardware events available on most CPUs
SUPPORTED_PMU_EVENTS = [
    "cycles",                   # CPU cycles (default)
    "cpu-cycles",              # Alias for cycles
    "instructions",            # Instructions executed
    "cache-misses",            # L3 cache misses
    "cache-references",        # L3 cache accesses
    "branch-misses",           # Mispredicted branches
    "branch-instructions",     # Branch instructions
    "stalled-cycles-frontend", # Frontend stall cycles
    "stalled-cycles-backend",  # Backend stall cycles (not supported on all CPUs)
]

# Event name aliases for normalization
# Some events have multiple names that map to the same counter
PMU_EVENT_ALIASES = {
    "cpu-cycles": "cycles",     # Normalize to canonical name
}

# Default event when none specified or all filtered out
DEFAULT_PMU_EVENT = "cycles"

# Timeout for perf command execution (seconds)
PERF_DETECTION_TIMEOUT = 5


# =============================================================================
# PMU EVENT DETECTION
# =============================================================================

def detect_supported_pmu_events(perf_path: str) -> List[str]:
    """
    Detect PMU events supported by the host CPU.
    
    Uses 'perf list pmu' to query hardware capabilities and filters
    for events that gprofiler can use. This ensures we only request
    events that the CPU actually supports.
    
    Args:
        perf_path: Full path to perf executable
        
    Returns:
        Sorted list of supported event names, e.g. ["cycles", "instructions"]
        Returns [DEFAULT_PMU_EVENT] if detection fails
        
    Example:
        >>> detect_supported_pmu_events("/usr/bin/perf")
        ['branch-instructions', 'cache-misses', 'cycles', 'instructions']
    """
    try:
        logger.info("Detecting supported PMU events on this host...")
        
        # Query hardware events from perf
        supported_events = _query_perf_hardware_events(perf_path)
        
        # Filter for events we care about
        filtered_events = _filter_supported_events(supported_events)
        
        # Normalize event names (handle aliases)
        normalized_events = _normalize_event_names(filtered_events)
        
        # Return sorted list for consistency
        result = sorted(list(normalized_events))
        
        if result:
            logger.info(
                f"Detected {len(result)} supported PMU events: {result}"
            )
            return result
        else:
            logger.warning(
                f"No PMU events detected, using default: "
                f"[{DEFAULT_PMU_EVENT}]"
            )
            return [DEFAULT_PMU_EVENT]
            
    except subprocess.TimeoutExpired:
        logger.warning(
            f"Timeout detecting PMU events (>{PERF_DETECTION_TIMEOUT}s), "
            f"using default: [{DEFAULT_PMU_EVENT}]"
        )
        return [DEFAULT_PMU_EVENT]
        
    except Exception as e:
        logger.warning(
            f"Error detecting PMU events: {e}, "
            f"using default: [{DEFAULT_PMU_EVENT}]"
        )
        return [DEFAULT_PMU_EVENT]


def _query_perf_hardware_events(perf_path: str) -> Set[str]:
    """
    Run 'perf list pmu' and parse output for hardware event names.
    
    Args:
        perf_path: Full path to perf executable
        
    Returns:
        Set of raw event names from perf output
    """
    result = subprocess.run(
        [perf_path, "list", "pmu"],
        capture_output=True,
        text=True,
        timeout=PERF_DETECTION_TIMEOUT
    )
    
    if result.returncode != 0:
        logger.warning(f"'perf list pmu' failed: {result.stderr}")
        return set()
    
    events = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        
        # Skip empty lines and headers
        if not line or line.startswith("List of"):
            continue
        
        # Event lines format:
        # "  branch-instructions OR branches    [Hardware event]"
        # "  cache-misses                       [Hardware cache event]"
        parts = line.split()
        if not parts:
            continue
        
        # Extract primary event name (first word)
        event_name = parts[0]
        events.add(event_name)
        
        # Extract alias if present (after "OR")
        if len(parts) >= 3 and parts[1].lower() == "or":
            alias_name = parts[2]
            events.add(alias_name)
    
    return events


def _filter_supported_events(available_events: Set[str]) -> Set[str]:
    """
    Filter for events that gprofiler supports.
    
    Args:
        available_events: Set of event names from perf
        
    Returns:
        Subset of available_events that are in SUPPORTED_PMU_EVENTS
    """
    return available_events.intersection(SUPPORTED_PMU_EVENTS)


def _normalize_event_names(events: Set[str]) -> Set[str]:
    """
    Normalize event names using PMU_EVENT_ALIASES.
    
    Args:
        events: Set of event names to normalize
        
    Returns:
        Set of normalized event names
        
    Example:
        >>> _normalize_event_names({"cpu-cycles", "instructions"})
        {"cycles", "instructions"}
    """
    normalized = set()
    for event in events:
        canonical_name = PMU_EVENT_ALIASES.get(event, event)
        normalized.add(canonical_name)
    return normalized


# =============================================================================
# EVENT VALIDATION
# =============================================================================

def validate_and_normalize_events(events: List[str]) -> List[str]:
    """
    Validate and normalize a list of PMU event names.
    
    This function:
    1. Filters out unsupported events (not in SUPPORTED_PMU_EVENTS)
    2. Normalizes aliases (cpu-cycles -> cycles)
    3. Removes duplicates
    4. Returns at least [DEFAULT_PMU_EVENT] as fallback
    
    Args:
        events: List of event names to validate
        
    Returns:
        List of valid, normalized, deduplicated event names
        Returns [DEFAULT_PMU_EVENT] if input is empty or all invalid
        
    Example:
        >>> validate_and_normalize_events(
        ...     ["cpu-cycles", "invalid-event", "instructions", "cycles"]
        ... )
        ['cycles', 'instructions']
    """
    if not events:
        return [DEFAULT_PMU_EVENT]
    
    valid_events = []
    for event in events:
        # Skip unsupported events
        if event not in SUPPORTED_PMU_EVENTS:
            continue
        
        # Normalize using aliases
        normalized = PMU_EVENT_ALIASES.get(event, event)
        
        # Avoid duplicates
        if normalized not in valid_events:
            valid_events.append(normalized)
    
    # Fallback to default if all events were filtered out
    return valid_events if valid_events else [DEFAULT_PMU_EVENT]
