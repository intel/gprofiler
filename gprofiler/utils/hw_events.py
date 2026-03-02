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
from gprofiler.utils import run_process
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
        raw_output = result.stdout

        # Decode bytes to string if necessary
        if isinstance(raw_output, bytes):
            output = raw_output.decode("utf-8", errors="replace")
        else:
            output = raw_output

        events: Dict[str, str] = {}
        current_type = "unknown"

        for line in output.splitlines():
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue

            # Detect section headers (lines that are ONLY section markers)
            if line.startswith("List of") or line.endswith(":"):
                # Section headers like "cpu:", "List of pre-defined events"
                continue

            # Parse event lines (format: "event_name [description]" or "event_name OR alias")
            # Extract event name (everything before the bracket or first whitespace block)
            match = re.match(r"^\s*([a-zA-Z0-9_\-:./]+(?:\s+OR\s+[a-zA-Z0-9_\-:./]+)?)\s*(?:\[(.+?)\])?", line)
            if match:
                event_part = match.group(1)
                event_tag = match.group(2)

                # Extract the primary event name (before "OR")
                event_name = event_part.split()[0] if event_part else None

                if event_name:
                    # Determine event type from tag if present
                    if event_tag:
                        # Treat Hardware event, Hardware cache event, and Kernel PMU event as hardware
                        if "Hardware" in event_tag or "Kernel PMU" in event_tag:
                            events[event_name] = "hardware"
                        elif "Software" in event_tag:
                            events[event_name] = "software"
                        elif "Tool event" in event_tag:
                            events[event_name] = "software"
                        elif "Tracepoint" in event_tag:
                            events[event_name] = "tracepoint"
                        else:
                            events[event_name] = current_type
                    else:
                        # No tag, use current section type
                        events[event_name] = current_type

        return events

    except CalledProcessError:
        # Cannot use logger here as it may be called before state initialization
        return {}
    except Exception:
        # Cannot use logger here as it may be called before state initialization
        return {}


def load_custom_events(hw_events_file: Optional[str] = None) -> Dict:
    """
    Load custom PMU event definitions from a JSON file.
    Returns dict with event definitions per platform.

    Args:
        hw_events_file: Path to the JSON file. If None, returns empty dict.
    """
    if hw_events_file is None:
        return {}

    try:
        json_path = Path(hw_events_file)
        if not json_path.exists():
            raise ValueError(f"Hardware events file not found: {hw_events_file}")

        with open(json_path, "r") as f:
            events = json.load(f)

        # Filter out metadata fields (starting with _)
        custom_events = {k: v for k, v in events.items() if not k.startswith("_")}
        return custom_events

    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in hardware events file: {e}")
    except Exception as e:
        raise ValueError(f"Failed to load hardware events file: {e}")


def get_event_type(event_name: str, perf_events: Dict[str, str], hw_events_file: Optional[str] = None) -> Optional[str]:
    """
    Get the type of an event (hardware, software, tracepoint, cache, custom).
    Returns event type or None if not found.
    """
    if event_name in perf_events:
        return perf_events[event_name]

    # Check if it's a custom event
    custom_events = load_custom_events(hw_events_file)
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


def validate_and_get_event_args(
    event_name: str, hypervisor_vendor: str, hw_events_file: Optional[str] = None
) -> List[str]:
    """
    Validate event and return perf arguments for it.

    Resolution order:
    1. Check perf list for built-in events
    2. Check custom events file (if specified)
    3. Raise error if not found

    Returns list like ["-e", "event_name:modifier"]
    """
    # Check if it's an uncore event (not supported for flamegraphs)
    if event_name.startswith("uncore_") or "/uncore_" in event_name:
        raise ValueError(
            f"Uncore event '{event_name}' is not supported for flamegraph generation. "
            f"Uncore events measure system-wide hardware activity and cannot be attributed to specific "
            f"processes/threads."
        )

    # First check perf list
    perf_events = get_perf_available_events()
    event_type = get_event_type(event_name, perf_events, hw_events_file)

    if event_type and event_type != "custom":
        # Found in perf list
        modifier = get_precise_modifier(event_name, event_type, hypervisor_vendor)
        event_with_modifier = f"{event_name}{modifier}"
        return ["-e", event_with_modifier]

    # Not in perf list, check custom events
    custom_events = load_custom_events(hw_events_file)
    if event_name not in custom_events:
        # Event not found anywhere
        available_builtin = list(perf_events.keys())[:10]  # Show first 10
        available_custom = list(custom_events.keys())

        error_msg = f"Event '{event_name}' not found in perf built-in events"
        if hw_events_file:
            error_msg += f" or custom events file ({hw_events_file})"
        error_msg += ".\n"
        error_msg += f"  Available built-in events (first 10): {available_builtin}\n"
        if available_custom:
            error_msg += f"  Available custom events: {available_custom}\n"
        else:
            error_msg += "  No custom events file provided. Use --hw-events-file to specify one.\n"
        error_msg += f"  Run '{perf_path()} list' to see all built-in events."

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
        raise ValueError(error_msg)

    # Get raw event code for this platform
    platform_config = event_config[platform]
    raw_event = platform_config.get("raw")

    if not raw_event:
        error_msg = f"Custom event '{event_name}' missing 'raw' field for platform '{platform}'"
        raise ValueError(error_msg)

    # Apply modifier for custom events (treated as hardware events)
    modifier = get_precise_modifier(event_name, "custom", hypervisor_vendor)
    event_with_modifier = f"{raw_event}{modifier}"

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
    except CalledProcessError:
        return False
    except Exception:
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
        return event_args

    # Failed - try fallback for VMs
    if is_vm and event_args[1].endswith(":p"):
        # Remove modifier
        event_without_modifier = event_args[1].rstrip(":p")
        fallback_args = ["-e", event_without_modifier]

        if test_perf_event_accessible(fallback_args):
            return fallback_args

    # No fallback worked
    error_msg = f"Cannot access perf event '{event_name}'. Check permissions and PMU availability."
    raise RuntimeError(error_msg)
