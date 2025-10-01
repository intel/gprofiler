# GPU Segfault Merge Logic Fix

## Problem Statement

On GPU machines, perf can segfault during symbol resolution due to GPU driver interactions. While gProfiler implements segfault detection and graceful recovery, this causes perf to return 0 samples. However, runtime profilers (py-spy, rbspy, async-profiler) may still collect valid samples.

The original merge logic had a critical flaw that would eliminate all runtime profiler samples when perf returned 0 samples, effectively losing valuable profiling data.

## Root Cause

The problematic code in `gprofiler/merge.py` was:

```python
# BROKEN LOGIC (before fix)
else:
    # do the scaling by the ratio of samples
    ratio = perf_samples_count / profile_samples_count  # When perf_samples_count = 0, ratio = 0
    profile.stacks = scale_sample_counts(profile.stacks, ratio)  # Scaling by 0 eliminates ALL samples!
```

### Issue Analysis

1. **GPU segfault occurs** during perf symbol resolution
2. **Perf returns 0 samples** after segfault recovery
3. **Runtime profilers** (py-spy, rbspy, async-profiler) still have valid samples
4. **Merge logic calculates ratio** = 0 / runtime_samples = 0.0
5. **Scaling by ratio 0** eliminates all runtime profiler samples
6. **Result**: Complete loss of profiling data despite runtime profilers working correctly

## Solution

The fix implements conditional scaling logic based on Pinterest's production-tested implementation:

```python
# FIXED LOGIC (after fix)
if process_perf is not None and perf_samples_count > 0 and ProfilingErrorStack.is_error_stack(profile.stacks):
    # runtime profiler returned an error stack; extend it with perf profiler stacks for the pid
    profile.stacks = ProfilingErrorStack.attach_error_to_stacks(process_perf.stacks, profile.stacks)
elif perf_samples_count > 0:
    # do the scaling by the ratio of samples: samples we received from perf for this process,
    # divided by samples we received from the runtime profiler of this process.
    ratio = perf_samples_count / profile_samples_count
    profile.stacks = scale_sample_counts(profile.stacks, ratio)
# else: perf_samples_count == 0, so preserve runtime profiler stacks unscaled
```

### Key Changes

1. **Added condition check**: Only scale when `perf_samples_count > 0`
2. **Preserve unscaled data**: When perf returns 0 samples, keep runtime profiler samples as-is
3. **Maintain error handling**: Error stacks are still processed correctly
4. **No data loss**: Runtime profiler data is preserved even when perf fails

## Impact

### Before Fix (Broken)
```
GPU Machine Scenario:
├── perf segfaults → 0 samples
├── py-spy collects → 1000 samples  
├── merge logic → ratio = 0/1000 = 0.0
├── scaling → 1000 * 0.0 = 0 samples
└── Result: ALL DATA LOST ❌
```

### After Fix (Working)
```
GPU Machine Scenario:
├── perf segfaults → 0 samples
├── py-spy collects → 1000 samples
├── merge logic → perf_samples_count = 0, skip scaling
├── preserve → keep 1000 samples unscaled
└── Result: ALL DATA PRESERVED ✅
```

## Testing

Comprehensive unit tests in `tests/test_merge_zero_samples_fix.py` cover:

- ✅ Zero perf samples preserve runtime stacks unscaled
- ✅ Non-zero perf samples scale correctly
- ✅ GPU segfault scenarios for Python, Ruby, Java
- ✅ Error stack handling with zero perf samples
- ✅ Demonstration of old logic data loss

### Test Results

```bash
python3 tests/test_merge_zero_samples_fix.py

# Output:
✅ Python process: Preserved 300 samples from runtime profiler
✅ Ruby process: Preserved 225 samples from runtime profiler  
✅ Java process: Preserved 450 samples from runtime profiler
❌ Old logic: 175 runtime samples → 0 samples (ALL LOST)
✅ Fixed logic: 175 runtime samples → 175 samples (ALL PRESERVED)
```

## Scenarios Addressed

### 1. GPU Segfault - Python Process
- **Situation**: py-spy collects 800 samples, perf segfaults
- **Before**: 800 samples → 0 samples (lost)
- **After**: 800 samples → 800 samples (preserved)

### 2. GPU Segfault - Java Process  
- **Situation**: async-profiler collects 1600 samples, perf segfaults
- **Before**: 1600 samples → 0 samples (lost)
- **After**: 1600 samples → 1600 samples (preserved)

### 3. GPU Segfault - Ruby Process
- **Situation**: rbspy collects 225 samples, perf segfaults  
- **Before**: 225 samples → 0 samples (lost)
- **After**: 225 samples → 225 samples (preserved)

### 4. Normal Operation (No GPU Issues)
- **Situation**: Both perf and runtime profilers collect samples
- **Before**: Normal scaling works
- **After**: Normal scaling still works (no regression)

## Implementation Details

### Files Modified

1. **`gprofiler/merge.py`** - Fixed merge logic to handle 0 perf samples
2. **`tests/test_merge_zero_samples_fix.py`** - Comprehensive test suite

### Code Changes

The fix changes the merge logic from:
```python
else:  # Always scale, even when perf_samples_count = 0
```

To:
```python
elif perf_samples_count > 0:  # Only scale when perf has samples
# else: preserve unscaled when perf has 0 samples
```

## Backward Compatibility

- ✅ **No breaking changes**: Existing functionality preserved
- ✅ **Normal scenarios**: Scaling still works when both profilers have samples  
- ✅ **Error handling**: Error stacks are processed correctly
- ✅ **Performance**: No performance impact

## Validation

### Manual Testing
```bash
# Simulate GPU segfault scenario
# 1. Runtime profiler collects samples
# 2. Perf returns 0 samples (segfault recovery)
# 3. Verify merge preserves runtime data

python3 tests/test_merge_zero_samples_fix.py
```

### Production Validation
This fix is based on Pinterest's production implementation that has been running successfully in their environment, handling GPU machines and other edge cases where perf may return 0 samples.

## Related Issues

- **Intel gProfiler Issue #992**: GPU Machine Segmentation Faults - CPU profiling on GPU machines
- **Pinterest Implementation**: Production-tested solution for merge logic handling
- **Segfault Detection**: Enhanced segfault detection and graceful recovery (separate from this fix)

## Future Considerations

1. **Enhanced Logging**: Add debug logging when preserving unscaled samples
2. **Metrics Collection**: Track scenarios where perf returns 0 samples
3. **Alternative Profiling**: Consider alternative approaches for GPU machines
4. **Perf Version Updates**: Address root cause by updating perf version (separate effort)

---

**Fix Status**: ✅ Complete and Tested  
**Based on**: Pinterest's production implementation  
**Addresses**: GPU segfault scenarios and merge logic robustness
