#
# Copyright (C) 2022 Intel Corporation
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

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from gprofiler.exceptions import CalledProcessError
from gprofiler.log import get_logger_adapter
from gprofiler.platform import get_cpu_model
from gprofiler.utils import resource_path, run_process
from gprofiler.utils.perf_process import perf_path

logger = get_logger_adapter(__name__)


@lru_cache(maxsize=None)
def get_perf_available_events() -> Dict[str, str]:
    """
    Run 'perf list' and parse available events with their types.
    Returns dict mapping event names to types (hardware, software, tracepoint, cache).
    """
    try:
        result = run_process([perf_path(), "list"], suppress_log=True)
        output = result.stdout

        events = {}
        current_type = "unknown"

        for line in output.splitlines():
            line = line.strip()

            # Detect section headers
            if "[Hardware event]" in line or "[Kernel PMU event]" in line:
                current_type = "hardware"
            elif "[Software event]" in line:
                current_type = "software"
            elif "[Hardware cache event]" in line:
                current_type = "cache"
            elif "[Tracepoint event]" in line or "Tracepoint" in line:
                current_type = "tracepoint"

            # Parse event lines (format: "event_name [description]" or "event_name")
            if line and not line.startswith("#") and "[" not in line:
                # Extract event name (first word before space or tab)
                match = re.match(r"^([a-zA-Z0-9_\-:.]+)", line)
                if match:
                    event_name = match.group(1)
                    events[event_name] = current_type

        logger.debug(f"Found {len(events)} available perf events")
        return events

    except CalledProcessError as e:
        logger.warning(f"Failed to run 'perf list': {e}")
        return {}
    except Exception as e:
        logger.warning(f"Error parsing 'perf list' output: {e}")
        return {}


@lru_cache(maxsize=None)
def load_custom_events() -> Dict:
    """
    Load custom PMU event definitions from hw_events.json.
    Returns dict with event definitions per platform.
    """
    try:
        json_path = Path(resource_path("hw_events.json"))
        if not json_path.exists():
            logger.debug(f"Custom events file not found: {json_path}")
            return {}

        with open(json_path, "r") as f:
            events = json.load(f)

        # Filter out metadata fields (starting with _)
        custom_events = {k: v for k, v in events.items() if not k.startswith("_")}
        logger.debug(f"Loaded {len(custom_events)} custom event definitions")
        return custom_events

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse hw_events.json: {e}")
        return {}
    except Exception as e:
        logger.error(f"Error loading custom events: {e}")
        return {}


def get_event_type(event_name: str, perf_events: Dict[str, str]) -> Optional[str]:
    """
    Get the type of an event (hardware, software, tracepoint, cache, custom).
    Returns event type or None if not found.
    """
    if event_name in perf_events:
        return perf_events[event_name]

    # Check if it's a custom event
    custom_events = load_custom_events()
    if event_name in custom_events:
        return "custom"

    return None


def get_precise_modifier(event_name: str, event_type: str, hypervisor_vendor: str) -> str:
    """
    Determine the precise event modifier based on event type and hypervisor status.

    Bare metal (hypervisor="NONE"):
    - cycles, instructions → :ppp
    - ocr.* → :p
    - other HW → :pp
    - SW/tracepoint → no modifier

    VM (hypervisor set):
    - all HW → :p
    - SW/tracepoint → no modifier
    """
    is_vm = hypervisor_vendor != "NONE"

    # Software events and tracepoints don't use PEBS modifiers
    if event_type in ("software", "tracepoint"):
        return ""

    # VM: all hardware events get :p
    if is_vm:
        return ":p"

    # Bare metal: different modifiers based on event
    if event_type in ("hardware", "cache", "custom"):
        # Special cases
        if event_name in ("cycles", "instructions"):
            return ":ppp"
        elif event_name.startswith("ocr.") or event_name.startswith("OCR."):
            return ":p"
        else:
            return ":pp"

    # Unknown type, no modifier
    return ""


def validate_and_get_event_args(event_name: str, hypervisor_vendor: str) -> List[str]:
    """
    Validate event and return perf arguments for it.

    Resolution order:
    1. Check perf list for built-in events
    2. Check hw_events.json for custom events
    3. Raise error if not found

    Returns list like ["-e", "event_name:modifier"]
    """
    # First check perf list
    perf_events = get_perf_available_events()
    event_type = get_event_type(event_name, perf_events)

    if event_type and event_type != "custom":
        # Found in perf list
        modifier = get_precise_modifier(event_name, event_type, hypervisor_vendor)
        event_with_modifier = f"{event_name}{modifier}"
        logger.info(
            f"Using built-in perf event",
            event=event_name,
            type=event_type,
            modifier=modifier,
            hypervisor=hypervisor_vendor,
        )
        return ["-e", event_with_modifier]

    # Not in perf list, check custom events
    custom_events = load_custom_events()
    if event_name not in custom_events:
        # Event not found anywhere
        available_builtin = list(perf_events.keys())[:10]  # Show first 10
        available_custom = list(custom_events.keys())

        error_msg = f"Event '{event_name}' not found in perf built-in events or custom event definitions.\n"
        error_msg += f"  Available built-in events (first 10): {available_builtin}\n"
        error_msg += f"  Available custom events: {available_custom}\n"
        error_msg += f"  Run '{perf_path()} list' to see all built-in events."

        logger.error(error_msg)
        raise ValueError(error_msg)

    # Found in custom events, get platform-specific config
    platform = get_cpu_model()
    event_config = custom_events[event_name]

    if platform not in event_config:
        supported_platforms = [k for k in event_config.keys() if not k.startswith("_")]
        error_msg = (
            f"Custom event '{event_name}' not supported on platform '{platform}'.\n"
            f"  Supported platforms: {supported_platforms}"
        )
        logger.error(error_msg)
        raise ValueError(error_msg)

    # Get raw event code for this platform
    platform_config = event_config[platform]
    raw_event = platform_config.get("raw")

    if not raw_event:
        error_msg = f"Custom event '{event_name}' missing 'raw' field for platform '{platform}'"
        logger.error(error_msg)
        raise ValueError(error_msg)

    # Apply modifier for custom events (treated as hardware events)
    modifier = get_precise_modifier(event_name, "custom", hypervisor_vendor)
    event_with_modifier = f"{raw_event}{modifier}"

    logger.info(
        f"Using custom PMU event",
        event=event_name,
        platform=platform,
        raw=raw_event,
        modifier=modifier,
        hypervisor=hypervisor_vendor,
    )

    return ["-e", event_with_modifier]


def test_perf_event_accessible(event_args: List[str]) -> bool:
    """
    Test if a perf event is accessible by running a quick perf record test.
    Returns True if accessible, False otherwise.
    """
    try:
        run_process(
            [perf_path(), "record", "-o", "/dev/null"] + event_args + ["--", "sleep", "0.1"],
            suppress_log=True,
        )
        return True
    except CalledProcessError as e:
        logger.debug(f"Perf event test failed: {e}")
        return False
    except Exception as e:
        logger.debug(f"Perf event test error: {e}")
        return False


def validate_event_with_fallback(event_name: str, event_args: List[str], hypervisor_vendor: str) -> List[str]:
    """
    Validate event accessibility with fallback for VMs.

    For VMs: if event with :p modifier fails, retry without modifier.
    For bare metal: no fallback, event must work as-is.

    Returns validated event args or raises error.
    """
    is_vm = hypervisor_vendor != "NONE"

    # Test the event
    if test_perf_event_accessible(event_args):
        logger.debug(f"Event {event_name} is accessible", event_args=event_args)
        return event_args

    # Failed - try fallback for VMs
    if is_vm and event_args[1].endswith(":p"):
        logger.warning(
            f"Event {event_name} with :p modifier failed in VM, retrying without modifier",
            hypervisor=hypervisor_vendor,
        )
        # Remove modifier
        event_without_modifier = event_args[1].rstrip(":p")
        fallback_args = ["-e", event_without_modifier]

        if test_perf_event_accessible(fallback_args):
            logger.info(f"Event {event_name} accessible without modifier")
            return fallback_args

    # No fallback worked
    error_msg = f"Cannot access perf event '{event_name}'. Check permissions and PMU availability."
    logger.error(error_msg, event_args=event_args, hypervisor=hypervisor_vendor)
    raise RuntimeError(error_msg)
