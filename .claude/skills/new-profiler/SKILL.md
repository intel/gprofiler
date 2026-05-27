---
name: new-profiler
description: Guide for adding a new profiler to gProfiler. Use when the user wants to add support for a new language runtime or profiling tool.
user-invocable: true
disable-model-invocation: true
---

## Adding a New Profiler to gProfiler

### Architecture Overview

```
gprofiler/profilers/
├── profiler_base.py      # Base class - extend this
├── registry.py           # @register_profiler decorator
├── factory.py            # Profiler instantiation
├── perf.py               # System profiler (reference: 19KB)
├── java.py               # Java profiler (reference: 67KB)
├── python.py             # Python py-spy profiler
├── python_ebpf.py        # Python PyPerf profiler
├── ruby.py               # Ruby rbspy profiler
├── php.py                # PHP phpspy profiler
├── dotnet.py             # .NET dotnet-trace profiler
└── node.py               # NodeJS profiler
```

### Step 1: Create Profiler Class

Create `gprofiler/profilers/<runtime>.py`:

```python
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

import logging
from typing import Optional

from gprofiler.gprofiler_types import ProcessToProfileData, ProfileData
from gprofiler.log import get_logger_adapter
from gprofiler.profilers.profiler_base import ProfilerBase
from gprofiler.profilers.registry import register_profiler

logger = get_logger_adapter(__name__)


@register_profiler(
    "MyRuntime",
    # Possible profiler names for CLI selection
    possible_modes=["myruntime", "disabled"],
    # Default mode
    default_mode="myruntime",
    # Supported architectures
    supported_archs=["x86_64", "aarch64"],
    # Profiler-specific CLI arguments
    profiler_arguments=[
        # Add any runtime-specific arguments
    ],
)
class MyRuntimeProfiler(ProfilerBase):
    """
    Profiler for MyRuntime applications.
    """

    # Frame suffix for this profiler (shown in flamegraph)
    FRAME_SUFFIX = "_[myrt]"

    def __init__(
        self,
        frequency: int,
        duration: int,
        stop_event,
        storage_dir: str,
        profile_spawned_processes: bool,
        *args,
        **kwargs,
    ):
        super().__init__(
            frequency=frequency,
            duration=duration,
            stop_event=stop_event,
            storage_dir=storage_dir,
            profile_spawned_processes=profile_spawned_processes,
        )
        # Initialize profiler-specific state

    def start(self) -> None:
        """Start the profiler. Called once at the beginning."""
        logger.info("Starting MyRuntime profiler")
        # Initialize profiling tools, attach to processes, etc.

    def stop(self) -> None:
        """Stop the profiler. Called once at the end."""
        logger.info("Stopping MyRuntime profiler")
        # Cleanup resources, detach from processes

    def snapshot(self) -> ProcessToProfileData:
        """
        Collect profiling data for one interval.
        Called periodically during profiling.

        Returns:
            Dict mapping process info to ProfileData
        """
        results: ProcessToProfileData = {}

        # Collect samples from your profiling tool
        # Parse stack traces
        # Build ProfileData for each process

        return results
```

### Step 2: Add CLI Arguments

In `gprofiler/main.py`, add runtime-specific arguments:

```python
# In the argument parser section
parser.add_argument(
    "--myruntime-mode",
    choices=["myruntime", "disabled"],
    default="myruntime",
    help="MyRuntime profiling mode",
)
parser.add_argument(
    "--no-myruntime",
    action="store_true",
    help="Disable MyRuntime profiling",
)
```

### Step 3: Add Tests

Create `tests/test_myruntime.py`:

```python
import pytest
from tests.conftest import AssertInCollapsed

class TestMyRuntimeProfiler:
    @pytest.mark.parametrize("version", ["1.0", "2.0"])
    def test_myruntime_profiling(
        self,
        application_docker_container,
        runtime_specific_args,
        assert_collapsed: AssertInCollapsed,
    ):
        """Test profiling of MyRuntime applications."""
        # Test implementation
        pass
```

### Step 4: Add Resources (if needed)

If your profiler requires external tools:

```
gprofiler/resources/
└── myruntime/
    ├── x86_64/
    │   └── profiler_tool
    └── aarch64/
        └── profiler_tool
```

Update build scripts in `scripts/` to include the tool.

### Step 5: Update Documentation

1. Update `README.md` with:
   - Runtime support in architecture table
   - Profiling options section
   - Frame format documentation

2. Add to frame suffix table:
   ```
   | MyRuntime | Per tool | `_[myrt]` |
   ```

### Key Patterns from Existing Profilers

**Process Discovery:**
```python
from gprofiler.utils.process import search_for_process

processes = search_for_process(
    lambda p: "myruntime" in p.cmdline(),
    self._stop_event,
)
```

**Resource Cleanup:**
```python
def stop(self) -> None:
    try:
        # Cleanup
    except Exception:
        logger.exception("Error stopping profiler")
    finally:
        # Ensure resources released
```

**Handling Stop Event:**
```python
def snapshot(self) -> ProcessToProfileData:
    if self._stop_event.is_set():
        return {}
    # Continue profiling...
```

### Testing Your Profiler

```bash
# Run profiler tests
cd tests && sudo python3 -m pytest -v test_myruntime.py

# Test with verbose output
sudo python3 -m gprofiler --myruntime-mode=myruntime -v -d 30 -o /tmp/output

# Verify frame format in output
cat /tmp/output/last_profile.col | grep "_[myrt]"
```

### Commit Message Pattern

```
Add <Runtime> profiler support (#PR_NUMBER)
<runtime>: Add version X.Y support (#PR_NUMBER)
```

---

## TODO: Skill Content to Add

- [ ] **Add complete profiler lifecycle diagram** - Visual flow of start/snapshot/stop
- [ ] **Add ProfilerBase method documentation** - All methods with signatures
- [ ] **Add example profiler implementations** - Annotated code from simple profilers
- [ ] **Add process discovery patterns** - Different ways to find target processes
- [ ] **Add stack trace parsing examples** - How to parse different profiler outputs
- [ ] **Add resource bundling guide** - How to add external binaries to build
- [ ] **Add profiler configuration patterns** - How CLI args flow to profiler
- [ ] **Add frame format specification** - Detailed frame suffix requirements

### Candidate New Profilers to Document

- [ ] **Rust** - Native profiling with symbolication
- [ ] **Erlang/BEAM** - Erlang VM profiling
- [ ] **Lua/LuaJIT** - Lua runtime profiling
