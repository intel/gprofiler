---
name: java-support
description: Add or update Java JDK version support. Use when the user needs to add support for a new JDK version or fix Java profiling issues.
user-invocable: true
---

## Adding Java JDK Version Support

**Context from history:** Java support is the most frequently updated profiler (17 commits in 3 years). New JDK releases require quick gProfiler updates.

### Supported JDK Versions

Currently supported: JDK 7+ (HotSpot-based JVMs)
- Oracle JDK
- OpenJDK (AdoptOpenJDK, Azul Zulu, Temurin, etc.)

Recent additions (from commit history):
- JDK 17, 18, 19, 21, 25
- Temurin JDK support

### Key Files to Modify

```
gprofiler/profilers/java.py           # Main Java profiler (67KB, 19 changes)
tests/test_java.py                    # Java tests (51KB, 11 changes)
gprofiler/resources/                  # async-profiler binaries
```

### Adding New JDK Version Support

**Step 1: Update Version Detection**

In `gprofiler/profilers/java.py`, find the JDK version detection logic:
```python
# Look for patterns like:
MIN_JDK_VERSION = ...
SUPPORTED_JDK_VERSIONS = [...]
```

**Step 2: Update async-profiler Compatibility**

Check if async-profiler supports the new JDK:
- async-profiler v3.0 added in recent updates
- DSO storage directory issues fixed for newer JDKs

**Step 3: Add Tests**

In `tests/test_java.py`:
```python
@pytest.mark.parametrize("jdk_version", [..., "new_version"])
def test_java_profiling_jdk_versions(jdk_version):
    ...
```

**Step 4: Update Test Docker Images**

From history: `openjdk:11-jdk` deprecated → moved to Temurin
```python
# In conftest.py or test_java.py
JAVA_TEST_IMAGES = {
    "8": "eclipse-temurin:8-jdk",
    "11": "eclipse-temurin:11-jdk",
    "17": "eclipse-temurin:17-jdk",
    "21": "eclipse-temurin:21-jdk",
    # Add new version
}
```

### Common Issues from History

**1. Directory Ownership for AsyncProfiler DSO**
```
# Commit: Support java profiling without root privilege (#987)
# Issue: DSO storage directory permissions
```

**2. Version Detection from Process Names**
```
# Some JDKs report version differently
# Check /proc/<pid>/cmdline parsing
```

**3. Rootless Container Profiling**
```
# Commit: Support java profiling without root privilege in a container (#987)
# Solution: Check process namespace and permissions
```

### Testing New JDK Support

```bash
# Test specific JDK version
cd tests && sudo python3 -m pytest -v test_java.py -k "jdk21"

# Test latest JVMs sanity
cd tests && sudo python3 -m pytest -v -k "test_sanity_latest_jvms"

# Full Java test suite
cd tests && sudo python3 -m pytest -v test_java.py
```

### Commit Message Pattern

From history, Java commits follow this pattern:
```
java: Add JDK <version> support (#PR_NUMBER)
java: Update min JDK <version> version (#PR_NUMBER)
java: Fix <issue> for JDK <version> (#PR_NUMBER)
```

### Java Profiling Options (from README)

```bash
# Disable Java profiling
--no-java
--java-mode disabled

# Disable buildid embedding
--no-java-async-profiler-buildids

# Skip version check (for testing)
--java-no-version-check

# Safe mode (empty to disable)
--java-safemode=
```

---

## TODO: Skill Content to Add

- [ ] **Add JDK version detection code** - Actual code snippets from java.py
- [ ] **Add async-profiler flags reference** - All async-profiler options used
- [ ] **Add JVM vendor compatibility matrix** - Oracle, Temurin, Azul, etc.
- [ ] **Add allocation profiling guide** - How to enable allocation profiling
- [ ] **Add JFR comparison** - When to use async-profiler vs JFR
- [ ] **Add container Java detection** - How Java processes are found in containers
- [ ] **Add frame format examples** - Example Java stack frames
- [ ] **Add troubleshooting decision tree** - Flowchart for Java profiling issues
