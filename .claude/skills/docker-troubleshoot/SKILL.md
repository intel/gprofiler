---
name: docker-troubleshoot
description: Troubleshoot Docker build and container issues. Use when the user has Docker build failures, container runtime issues, or needs help with Dockerfiles.
allowed-tools: Bash(docker *) Bash(cat *Dockerfile*) Read Grep
---

## gProfiler Docker Troubleshooting

### Docker Environment

```!
docker --version 2>/dev/null || echo "Docker not found"
docker info 2>/dev/null | grep -E "Server Version|Storage Driver|Operating System" || echo "Docker daemon not running"
```

### Key Dockerfiles

| File | Purpose | Build Script |
|------|---------|--------------|
| `container.Dockerfile` | Full container with bundled profilers | `scripts/build_x86_64_container.sh` |
| `executable.Dockerfile` | PyInstaller executable build | `scripts/build_x86_64_executable.sh` |

### Common Build Commands

```bash
# Build container (x86_64)
./scripts/build_x86_64_container.sh -t gprofiler:dev

# Build executable
./scripts/build_x86_64_executable.sh

# Build with no cache (for debugging)
docker build --no-cache -f container.Dockerfile -t gprofiler:test .

# Build specific stage
docker build --target builder -f container.Dockerfile -t gprofiler:builder .
```

### Troubleshooting Steps

#### Build Failures

1. **Check Dockerfile syntax**
   ```bash
   ./dockerfile_lint.sh
   ```

2. **Build with verbose output**
   ```bash
   docker build --progress=plain -f container.Dockerfile .
   ```

3. **Debug specific stage**
   ```bash
   # Build up to failing stage
   docker build --target <stage-name> -f container.Dockerfile -t debug:stage .
   # Inspect
   docker run -it debug:stage /bin/bash
   ```

#### Runtime Issues

```bash
# Run with full privileges (for profiling)
docker run --privileged -it gprofiler:dev /bin/bash

# Check required capabilities
docker run --cap-add SYS_ADMIN --cap-add SYS_PTRACE -it gprofiler:dev

# Mount host for debugging
docker run -v /tmp:/host-tmp --privileged -it gprofiler:dev
```

#### Image Size Issues

```bash
# Analyze image layers
docker history gprofiler:dev

# Check image size
docker images gprofiler:dev

# Find large files
docker run --rm gprofiler:dev du -sh /* 2>/dev/null | sort -h
```

### Multi-Architecture Builds

```bash
# x86_64
./scripts/build_x86_64_container.sh -t gprofiler:x86

# ARM64 (requires QEMU or ARM host)
./scripts/build_aarch64_container.sh -t gprofiler:arm64

# Buildx for multi-arch (if configured)
docker buildx build --platform linux/amd64,linux/arm64 -f container.Dockerfile .
```

### Instructions

1. Identify the specific error message
2. Determine if it's build-time or runtime
3. Check relevant Dockerfile section
4. Suggest minimal fix or workaround
