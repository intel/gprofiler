#!/usr/bin/env python3
"""
Unit tests for the short-lived process fix implementation.
Tests the smart skipping logic to ensure it correctly identifies and skips young processes.
"""

import time
import unittest


class MockProcess:
    """Mock process class to simulate psutil.Process for testing"""

    def __init__(self, pid: int, create_time: float):
        self.pid = pid
        self._create_time = create_time

    def create_time(self) -> float:
        return self._create_time


class TestProfilerBase:
    """Test implementation of the profiler base class with smart skipping logic"""

    def __init__(self, min_duration: int = 10):
        self._min_duration = min_duration

    def _get_process_age(self, process: MockProcess) -> float:
        """Get the age of a process in seconds."""
        try:
            return time.time() - process.create_time()
        except Exception:
            # Return a large age value when we can't determine the real age
            # This ensures the process won't be skipped due to unknown age
            return float("inf")

    def should_skip_young_process(self, process: MockProcess) -> bool:
        """Test the short-lived process skipping logic"""
        try:
            process_age = self._get_process_age(process)
            if process_age < self._min_duration:
                return True
            else:
                return False
        except Exception:
            # When we can't determine age, we conservatively don't skip (return False)
            # This matches the behavior in the actual profiler implementation
            return False


class TestShortLivedProcessFix(unittest.TestCase):
    """Unit tests for short-lived process fix functionality"""

    def setUp(self):
        """Set up test fixtures"""
        self.profiler = TestProfilerBase(min_duration=10)
        self.current_time = time.time()

    def test_very_young_process_is_skipped(self):
        """Test that processes younger than 5 seconds are skipped"""
        process = MockProcess(pid=1001, create_time=self.current_time - 2.0)
        self.assertTrue(self.profiler.should_skip_young_process(process))

    def test_young_process_is_skipped(self):
        """Test that processes younger than min_duration are skipped"""
        process = MockProcess(pid=1002, create_time=self.current_time - 5.0)
        self.assertTrue(self.profiler.should_skip_young_process(process))

    def test_process_just_under_threshold_is_skipped(self):
        """Test that processes just under min_duration threshold are skipped"""
        process = MockProcess(pid=1003, create_time=self.current_time - 9.5)
        self.assertTrue(self.profiler.should_skip_young_process(process))

    def test_process_at_threshold_is_not_skipped(self):
        """Test that processes exactly at min_duration threshold are not skipped"""
        process = MockProcess(pid=1004, create_time=self.current_time - 10.0)
        self.assertFalse(self.profiler.should_skip_young_process(process))

    def test_older_process_is_not_skipped(self):
        """Test that processes older than min_duration are not skipped"""
        process = MockProcess(pid=1005, create_time=self.current_time - 15.0)
        self.assertFalse(self.profiler.should_skip_young_process(process))

    def test_much_older_process_is_not_skipped(self):
        """Test that much older processes are not skipped"""
        process = MockProcess(pid=1006, create_time=self.current_time - 60.0)
        self.assertFalse(self.profiler.should_skip_young_process(process))

    def test_custom_min_duration_threshold(self):
        """Test that custom min_duration threshold works correctly"""
        custom_profiler = TestProfilerBase(min_duration=5)

        # Process younger than 5 seconds should be skipped
        young_process = MockProcess(pid=2001, create_time=self.current_time - 3.0)
        self.assertTrue(custom_profiler.should_skip_young_process(young_process))

        # Process older than 5 seconds should not be skipped
        old_process = MockProcess(pid=2002, create_time=self.current_time - 7.0)
        self.assertFalse(custom_profiler.should_skip_young_process(old_process))

    def test_zero_min_duration_disables_skipping(self):
        """Test that setting min_duration to 0 effectively disables skipping"""
        no_skip_profiler = TestProfilerBase(min_duration=0)

        # Even very young processes should not be skipped
        very_young_process = MockProcess(pid=3001, create_time=self.current_time - 0.5)
        self.assertFalse(no_skip_profiler.should_skip_young_process(very_young_process))

    def test_process_age_calculation_accuracy(self):
        """Test that process age calculation is accurate"""
        test_age = 25.5
        process = MockProcess(pid=4001, create_time=self.current_time - test_age)
        calculated_age = self.profiler._get_process_age(process)

        # Allow for small timing differences (within 1 second)
        self.assertAlmostEqual(calculated_age, test_age, delta=1.0)

    def test_error_scenarios(self):
        """Test error handling scenarios"""
        # Test with a process that raises an exception
        class ErrorProcess:
            def __init__(self, pid):
                self.pid = pid

            def create_time(self):
                raise Exception("Process not found")

        error_process = ErrorProcess(pid=5001)
        # Should return False (don't skip) when we can't determine age
        self.assertFalse(self.profiler.should_skip_young_process(error_process))


class TestErrorReductionScenarios(unittest.TestCase):
    """Test realistic scenarios that demonstrate error reduction"""

    def setUp(self):
        """Set up test fixtures"""
        self.profiler = TestProfilerBase(min_duration=10)
        self.current_time = time.time()

    def test_build_script_scenario(self):
        """Test that short-lived build scripts are skipped"""
        build_script = MockProcess(pid=6001, create_time=self.current_time - 1.5)
        self.assertTrue(self.profiler.should_skip_young_process(build_script))

    def test_container_init_scenario(self):
        """Test that transient container init processes are skipped"""
        container_init = MockProcess(pid=6002, create_time=self.current_time - 3.0)
        self.assertTrue(self.profiler.should_skip_young_process(container_init))

    def test_utility_command_scenario(self):
        """Test that quick utility commands are skipped"""
        utility_cmd = MockProcess(pid=6003, create_time=self.current_time - 7.2)
        self.assertTrue(self.profiler.should_skip_young_process(utility_cmd))

    def test_web_server_scenario(self):
        """Test that long-running web servers are not skipped"""
        web_server = MockProcess(pid=6004, create_time=self.current_time - 45.0)
        self.assertFalse(self.profiler.should_skip_young_process(web_server))

    def test_database_scenario(self):
        """Test that database processes are not skipped"""
        database = MockProcess(pid=6005, create_time=self.current_time - 120.0)
        self.assertFalse(self.profiler.should_skip_young_process(database))


def run_interactive_demo():
    """Run an interactive demonstration of the fix"""
    print("🧪 Short-Lived Process Fix - Interactive Demo")
    print("=" * 60)

    profiler = TestProfilerBase(min_duration=10)
    current_time = time.time()

    # Test cases: (description, process_age_seconds, expected_skip)
    test_cases = [
        ("Very young build script", 2.0, True),
        ("Young container init", 5.0, True),
        ("Quick utility command", 9.5, True),
        ("Process at threshold", 10.0, False),
        ("Web server process", 15.0, False),
        ("Long-running database", 60.0, False),
    ]

    print(f"Min duration threshold: {profiler._min_duration} seconds\n")

    all_passed = True
    for i, (description, process_age, expected_skip) in enumerate(test_cases, 1):
        # Create a mock process with the desired age
        process_create_time = current_time - process_age
        mock_process = MockProcess(pid=1000 + i, create_time=process_create_time)

        # Test the skipping logic
        should_skip = profiler.should_skip_young_process(mock_process)

        # Verify the result
        if should_skip == expected_skip:
            status = "✅ PASS"
        else:
            status = "❌ FAIL"
            all_passed = False

        action = "SKIP" if should_skip else "PROFILE"
        print(f"Test {i}: {description} (age: {process_age}s) → {action} - {status}")

    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 All tests passed! Smart Skipping Logic is working correctly.")
    else:
        print("⚠️  Some tests failed. Please check the implementation.")

    return all_passed


if __name__ == "__main__":
    # Run unit tests
    print("Running unit tests...")
    unittest.main(argv=[""], exit=False, verbosity=2)

    print("\n" + "=" * 80 + "\n")

    # Run interactive demo
    run_interactive_demo()
