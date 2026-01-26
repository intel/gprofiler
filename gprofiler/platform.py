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
import platform
import sys
from functools import lru_cache

WINDOWS_PLATFORM_NAME = "win32"
LINUX_PLATFORM_NAME = "linux"


@lru_cache(maxsize=None)
def is_windows() -> bool:
    return sys.platform == WINDOWS_PLATFORM_NAME


@lru_cache(maxsize=None)
def is_linux() -> bool:
    return sys.platform == LINUX_PLATFORM_NAME


@lru_cache(maxsize=None)
def is_aarch64() -> bool:
    return platform.machine() == "aarch64"


@lru_cache(maxsize=None)
def get_cpu_model() -> str:
    """
    Detect Intel CPU model for custom PMU event support.
    Returns platform code: ICX, SPR, EMR, GNR, or UNKNOWN.
    """
    if not is_linux():
        return "UNKNOWN"

    try:
        with open("/proc/cpuinfo", "r") as f:
            cpu_family = None
            model = None

            for line in f:
                if line.startswith("cpu family"):
                    cpu_family = int(line.split(":")[1].strip())
                elif line.startswith("model") and not line.startswith("model name"):
                    model = int(line.split(":")[1].strip())

                # Once we have both, we can determine the platform
                if cpu_family is not None and model is not None:
                    break

            # All supported platforms are Intel Family 6
            if cpu_family != 6:
                return "UNKNOWN"

            # Map model numbers to platform codes
            model_to_platform = {
                106: "ICX",  # Ice Lake Server
                143: "SPR",  # Sapphire Rapids
                207: "EMR",  # Emerald Rapids
                173: "GNR",  # Granite Rapids
            }

            return model_to_platform.get(model, "UNKNOWN")

    except Exception:
        return "UNKNOWN"


@lru_cache(maxsize=None)
def get_hypervisor_vendor() -> str:
    """
    Detect hypervisor vendor using CPUID.
    Returns hypervisor vendor string (e.g., "KVMKVMKVM", "VMwareVMware") or "NONE" for bare metal.
    """
    if not is_linux():
        return "NONE"

    try:
        # Try to use cpuid if available
        # CPUID leaf 0x1, ECX bit 31 indicates hypervisor presence
        # If present, CPUID leaf 0x40000000 returns vendor string in EBX, ECX, EDX

        # We need to read from /dev/cpu/*/cpuid or use inline assembly
        # For simplicity, we'll check if the hypervisor bit is set via /proc/cpuinfo flags
        # and then try to read the vendor string

        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("flags") or line.startswith("Features"):
                    flags = line.split(":")[1].strip()
                    if "hypervisor" in flags:
                        # Hypervisor detected, try to get vendor
                        return _read_hypervisor_vendor()
                    else:
                        return "NONE"

        return "NONE"

    except Exception:
        return "NONE"


def _read_hypervisor_vendor() -> str:
    """
    Read hypervisor vendor string from CPUID leaf 0x40000000.
    The vendor string is 12 characters: EBX (4 bytes) + ECX (4 bytes) + EDX (4 bytes).
    """
    try:
        import cpuid
        import struct

        # Execute CPUID leaf 0x40000000 for hypervisor vendor
        eax, ebx, ecx, edx = cpuid.cpuid(0x40000000, 0)

        # Vendor string is in EBX, ECX, EDX (12 characters total)
        vendor_bytes = struct.pack("<III", ebx, ecx, edx)
        vendor_string = vendor_bytes.decode("ascii", errors="replace").rstrip("\x00")

        if vendor_string and vendor_string.strip():
            return vendor_string
        else:
            return "VM-UNKNOWN"

    except Exception:
        return "VM-UNKNOWN"
