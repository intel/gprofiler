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
PMU Events Manager - Singleton for managing PMU event detection.

This module provides a singleton class that:
- Detects supported PMU events once at startup
- Caches results in memory for the agent lifetime
- Provides thread-safe access to supported events

Usage:
    from gprofiler.profilers.pmu_manager import get_pmu_manager
    
    manager = get_pmu_manager()
    events = manager.get_supported_events("/path/to/perf")
"""

import logging
import threading
from typing import List, Optional

from gprofiler.profilers.perf_events import (
    detect_supported_pmu_events,
    DEFAULT_PMU_EVENT,
)
from gprofiler.utils import resource_path

logger = logging.getLogger(__name__)


# =============================================================================
# SINGLETON MANAGER
# =============================================================================

class PMUEventsManager:
    """
    Singleton manager for PMU event detection and caching.
    
    Ensures PMU event detection happens only once per agent lifetime,
    avoiding repeated expensive 'perf list pmu' calls.
    
    Thread-safe: Uses lock for initialization.
    """
    
    _instance: Optional['PMUEventsManager'] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Ensure only one instance exists (singleton pattern)."""
        if cls._instance is None:
            with cls._lock:
                # Double-check locking pattern
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize manager (only runs once)."""
        if self._initialized:
            return
        
        self._supported_events: List[str] = []
        self._detection_attempted = False
        self._initialized = True
        
        logger.debug("PMUEventsManager singleton initialized")
    
    def get_supported_events(
        self, 
        perf_path: Optional[str] = None
    ) -> List[str]:
        """
        Get list of supported PMU events for this host.
        
        Detects events on first call, then returns cached result.
        
        Args:
            perf_path: Optional path to perf executable
                      If not provided, uses resource_path("perf")
        
        Returns:
            Copy of cached supported events list
            
        Example:
            >>> manager = PMUEventsManager()
            >>> events = manager.get_supported_events()
            >>> print(events)
            ['cycles', 'instructions', 'cache-misses']
        """
        if not self._detection_attempted:
            self._detect_and_cache_events(perf_path)
        
        # Return copy to prevent external modification
        return self._supported_events.copy()
    
    def refresh_events(self, perf_path: Optional[str] = None):
        """
        Force re-detection of PMU events.
        
        Useful if system configuration changes (rare in production).
        
        Args:
            perf_path: Optional path to perf executable
        """
        logger.info("Forcing PMU events re-detection...")
        self._detection_attempted = False
        self._supported_events = []
        self.get_supported_events(perf_path)
    
    def _detect_and_cache_events(self, perf_path: Optional[str]):
        """
        Internal method to detect events once and cache result.
        
        Thread-safe: Only one thread will perform detection.
        """
        with self._lock:
            # Double-check: another thread might have completed detection
            if self._detection_attempted:
                return
            
            self._detection_attempted = True
            
            # Resolve perf path if not provided
            if perf_path is None:
                perf_path = self._get_perf_path()
            
            # Detect and cache
            self._supported_events = detect_supported_pmu_events(perf_path)
            
            logger.info(
                f"PMU events cached in memory: {self._supported_events}"
            )
    
    def _get_perf_path(self) -> str:
        """Get path to bundled perf executable."""
        try:
            return resource_path("perf")
        except Exception as e:
            logger.warning(f"Could not find perf executable: {e}")
            # Return fallback that will trigger safe default in detection
            return "/usr/bin/perf"


# =============================================================================
# CONVENIENCE FUNCTION
# =============================================================================

def get_pmu_manager() -> PMUEventsManager:
    """
    Get the PMUEventsManager singleton instance.
    
    Convenience function for cleaner imports and usage.
    
    Returns:
        The singleton PMUEventsManager instance
        
    Example:
        >>> from gprofiler.profilers.pmu_manager import get_pmu_manager
        >>> manager = get_pmu_manager()
        >>> events = manager.get_supported_events()
    """
    return PMUEventsManager()
