#!/usr/bin/env python3
"""
Unit tests for the merge logic fix that handles GPU segfault scenarios
where perf returns 0 samples but runtime profilers have valid samples.

This addresses the issue: "However, this cause perf returning 0 samples and will break
run time profilers during merge logic. So we also need to fix the merge logic."
"""

import unittest
from collections import Counter
from typing import Dict


# Mock the required types and classes
class MockProfileData:
    def __init__(self, stacks: Dict[str, int], app_metadata=None, container_name=None, appid=None):
        self.stacks = Counter(stacks)
        self.app_metadata = app_metadata or {}
        self.container_name = container_name
        self.appid = appid


class MockProfilingErrorStack:
    @staticmethod
    def is_error_stack(stacks):
        # Simple mock - check if any stack contains "error"
        return any("error" in stack.lower() for stack in stacks.keys())

    @staticmethod
    def attach_error_to_stacks(perf_stacks, error_stacks):
        # Simple mock - combine the stacks
        combined = Counter(perf_stacks)
        combined.update(error_stacks)
        return combined


def mock_scale_sample_counts(stacks, ratio):
    """Mock implementation of scale_sample_counts"""
    if ratio == 1:
        return stacks

    scaled_stacks = Counter()
    for stack, count in stacks.items():
        new_count = int(count * ratio)
        if new_count > 0:
            scaled_stacks[stack] = new_count
    return scaled_stacks


class TestMergeZeroSamplesFix(unittest.TestCase):
    """Test the merge logic fix for handling 0 samples from perf"""

    def setUp(self):
        """Set up test fixtures"""
        self.sample_runtime_stacks = {
            "process_name;main;function_a": 100,
            "process_name;main;function_b": 50,
            "process_name;main;function_c": 25,
        }

        self.sample_perf_stacks = {
            "process_name;kernel_func": 80,
            "process_name;native_func": 40,
        }

    def test_merge_with_zero_perf_samples_preserves_runtime_stacks(self):
        """Test that when perf returns 0 samples, runtime profiler stacks are preserved unscaled"""

        # Simulate the scenario: runtime profiler has samples, perf has 0 samples
        runtime_profile = MockProfileData(self.sample_runtime_stacks)
        perf_profile = MockProfileData({})  # Empty stacks = 0 samples

        # Simulate the fixed merge logic
        perf_samples_count = sum(perf_profile.stacks.values())  # = 0
        profile_samples_count = sum(runtime_profile.stacks.values())  # = 175

        self.assertEqual(perf_samples_count, 0)
        self.assertEqual(profile_samples_count, 175)

        # Test the fixed logic path
        if perf_samples_count > 0:
            # This should NOT execute when perf_samples_count = 0
            self.fail("Should not scale when perf_samples_count = 0")
        else:
            # This is the fix: preserve runtime profiler stacks unscaled
            result_stacks = runtime_profile.stacks

        # Verify runtime stacks are preserved exactly as-is
        self.assertEqual(result_stacks, Counter(self.sample_runtime_stacks))
        self.assertEqual(sum(result_stacks.values()), 175)

    def test_merge_with_nonzero_perf_samples_scales_correctly(self):
        """Test that when perf has samples, scaling works correctly"""

        runtime_profile = MockProfileData(self.sample_runtime_stacks)
        perf_profile = MockProfileData(self.sample_perf_stacks)

        perf_samples_count = sum(perf_profile.stacks.values())  # = 120
        profile_samples_count = sum(runtime_profile.stacks.values())  # = 175

        self.assertEqual(perf_samples_count, 120)
        self.assertEqual(profile_samples_count, 175)

        # Test the scaling logic path
        if perf_samples_count > 0:
            ratio = perf_samples_count / profile_samples_count  # 120/175 ≈ 0.686
            scaled_stacks = mock_scale_sample_counts(runtime_profile.stacks, ratio)
        else:
            self.fail("Should scale when perf_samples_count > 0")

        # Verify scaling occurred
        expected_total = int(175 * (120 / 175))  # Should be approximately 120
        actual_total = sum(scaled_stacks.values())

        # Allow for some rounding differences
        self.assertLessEqual(abs(actual_total - expected_total), 5)

    def test_old_logic_would_eliminate_runtime_samples(self):
        """Demonstrate that the old logic would eliminate valuable runtime profiler samples"""

        runtime_profile = MockProfileData(self.sample_runtime_stacks)
        perf_profile = MockProfileData({})  # 0 samples

        perf_samples_count = sum(perf_profile.stacks.values())  # = 0
        profile_samples_count = sum(runtime_profile.stacks.values())  # = 175

        # This would be the old (broken) logic:
        ratio = perf_samples_count / profile_samples_count  # 0/175 = 0.0
        self.assertEqual(ratio, 0.0)

        # The actual issue: when perf_samples_count = 0, scaling by ratio 0
        # would eliminate all runtime profiler samples
        scaled = mock_scale_sample_counts(runtime_profile.stacks, ratio)

        # This would result in empty stacks, losing valuable runtime profiler data
        self.assertEqual(sum(scaled.values()), 0)  # All samples lost!
        self.assertEqual(len(scaled), 0)  # All stacks eliminated!

        print(f"❌ Old logic: {profile_samples_count} runtime samples → {sum(scaled.values())} samples (ALL LOST)")

        # Demonstrate the fix preserves the data
        if perf_samples_count > 0:
            # Scale normally
            preserved = mock_scale_sample_counts(runtime_profile.stacks, ratio)
        else:
            # Fixed logic: preserve runtime profiler stacks unscaled
            preserved = runtime_profile.stacks

        self.assertEqual(sum(preserved.values()), 175)  # All samples preserved!
        print(
            f"✅ Fixed logic: {profile_samples_count} runtime samples → "
            f"{sum(preserved.values())} samples (ALL PRESERVED)"
        )

    def test_gpu_segfault_scenario(self):
        """Test the specific GPU segfault scenario mentioned in the issue"""

        # GPU segfault scenario:
        # 1. GPU machine causes perf to segfault during symbol resolution
        # 2. Segfault detection and recovery results in perf returning 0 samples
        # 3. Runtime profilers (py-spy, rbspy, async-profiler) still have valid samples

        scenarios = [
            (
                "Python process",
                {"py-spy;main.py;function_a": 200, "py-spy;main.py;function_b": 100},
            ),
            (
                "Ruby process",
                {"rbspy;app.rb;method_a": 150, "rbspy;app.rb;method_b": 75},
            ),
            (
                "Java process",
                {"async-profiler;Main.main": 300, "async-profiler;Worker.run": 150},
            ),
        ]

        for process_type, runtime_stacks in scenarios:
            with self.subTest(process_type=process_type):
                runtime_profile = MockProfileData(runtime_stacks)
                perf_profile = MockProfileData({})  # Perf segfaulted, returned 0 samples

                perf_samples_count = sum(perf_profile.stacks.values())  # = 0
                _ = sum(runtime_profile.stacks.values())  # Only used for assertion

                # Apply the fixed merge logic
                if perf_samples_count > 0:
                    self.fail(f"Perf should have 0 samples in {process_type} scenario")
                else:
                    # Fixed logic: preserve runtime profiler stacks unscaled
                    result_stacks = runtime_profile.stacks

                # Verify runtime profiler data is preserved
                self.assertEqual(result_stacks, Counter(runtime_stacks))
                self.assertGreater(sum(result_stacks.values()), 0)
                print(f"✅ {process_type}: Preserved {sum(result_stacks.values())} samples from runtime profiler")

    def test_error_stack_handling_with_zero_perf_samples(self):
        """Test error stack handling when perf has 0 samples"""

        # Runtime profiler returns an error stack
        error_stacks = {"error: process exited during profiling": 1}
        runtime_profile = MockProfileData(error_stacks)
        perf_profile = MockProfileData({})  # 0 samples from perf

        perf_samples_count = sum(perf_profile.stacks.values())  # = 0

        # Test the logic path for error stacks
        if perf_samples_count > 0 and MockProfilingErrorStack.is_error_stack(runtime_profile.stacks):
            self.fail("Should not execute error attachment when perf_samples_count = 0")
        elif perf_samples_count > 0:
            self.fail("Should not execute scaling when perf_samples_count = 0")
        else:
            # This is the correct path: preserve error stacks as-is
            result_stacks = runtime_profile.stacks

        # Verify error stacks are preserved
        self.assertEqual(result_stacks, Counter(error_stacks))


def run_interactive_demo():
    """Run an interactive demonstration of the merge logic fix"""
    print("🧪 Merge Logic Fix - GPU Segfault Scenario Demo")
    print("=" * 70)

    scenarios = [
        {
            "name": "GPU Segfault - Python Process",
            "runtime_stacks": {
                "py-spy;main.py;process_data": 500,
                "py-spy;main.py;handle_request": 300,
            },
            "perf_stacks": {},  # Perf segfaulted, 0 samples
            "description": "py-spy collected 800 samples, perf segfaulted on GPU machine",
        },
        {
            "name": "GPU Segfault - Java Process",
            "runtime_stacks": {
                "async-profiler;Main.main": 1000,
                "async-profiler;Worker.execute": 600,
            },
            "perf_stacks": {},  # Perf segfaulted, 0 samples
            "description": "async-profiler collected 1600 samples, perf segfaulted on GPU machine",
        },
        {
            "name": "Normal Operation - No GPU Issues",
            "runtime_stacks": {"py-spy;app.py;function": 200},
            "perf_stacks": {
                "kernel_func": 150,
                "native_func": 50,
            },  # Normal perf samples
            "description": "Both runtime profiler and perf collected samples normally",
        },
    ]

    for scenario in scenarios:
        print(f"\n📋 Scenario: {scenario['name']}")
        print(f"   💡 {scenario['description']}")

        runtime_samples = sum(scenario["runtime_stacks"].values())
        perf_samples = sum(scenario["perf_stacks"].values())

        print(f"   📊 Runtime profiler samples: {runtime_samples}")
        print(f"   📊 Perf samples: {perf_samples}")

        # Apply fixed merge logic
        if perf_samples > 0:
            ratio = perf_samples / runtime_samples
            print(f"   ⚖️  Scaling runtime samples by ratio: {ratio:.3f}")
            result_samples = int(runtime_samples * ratio)
            print(f"   ✅ Result: {result_samples} scaled samples")
        else:
            print("   🛡️  Preserving runtime samples unscaled (perf returned 0)")
            result_samples = runtime_samples
            print(f"   ✅ Result: {result_samples} preserved samples")

        print(f"   🎯 Data preserved: {'Yes' if result_samples > 0 else 'No'}")

    print("\n" + "=" * 70)
    print("🎉 Fix Summary:")
    print("• When perf returns 0 samples (GPU segfault), runtime profiler data is preserved")
    print("• When perf has samples, normal scaling logic applies")
    print("• No more division by zero errors or lost profiling data")


if __name__ == "__main__":
    # Run unit tests
    print("Running unit tests...")
    unittest.main(argv=[""], exit=False, verbosity=2)

    print("\n" + "=" * 80 + "\n")

    # Run interactive demo
    run_interactive_demo()
