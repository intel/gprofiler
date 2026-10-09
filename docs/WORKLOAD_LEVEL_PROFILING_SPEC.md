# Workload-Level Profiling Spec for gProfiler

## Purpose

This spec defines the agent-side contract for workload-level profiling support.
It is intended to support spec-driven development for future heartbeat, targeting,
and Kubernetes metadata changes in the `gprofiler` repo.

The key idea is that the agent keeps host-command execution semantics, but
publishes richer workload inventory through heartbeat metadata so the backend can
resolve workload selections into concrete host and process targets.

## Scope

This spec covers:

- heartbeat payload extensions sent by the gProfiler agent
- best-effort workload inventory discovery from container runtime metadata
- compatibility constraints with the existing host-based command queue
- expected follow-up workflow for spec-driven development

This spec does not redefine profile storage, flamegraph rendering, or
Performance Studio UI behavior beyond the contract the agent must satisfy.

## Goals

1. Allow Performance Studio to target namespaces, pods, containers, and
   processes without replacing the existing host-based command-delivery model.
2. Keep the agent implementation additive and backward compatible.
3. Make workload targeting safe even when Kubernetes metadata is partial.
4. Ensure future changes start by updating this spec before code changes land.

## Non-Goals

- introducing a new agent-side workload scheduler
- turning commands into pod-native or container-native execution primitives
- guaranteeing perfect Kubernetes workload-name inference across all runtimes
- supporting workload discovery without a visible container runtime

## Design Summary

The gProfiler agent continues to:

- receive commands per `(hostname, service_name)`
- execute profiling based on resolved PIDs or host-wide settings
- report command completion via the existing heartbeat control plane

The new behavior is:

- collect best-effort container inventory during heartbeat generation
- attach Kubernetes-aware metadata when available
- publish process membership by container so the backend can resolve workload
  selections into host/PID mappings before command creation

## Heartbeat Contract

The heartbeat payload remains host-centric but includes optional workload fields:

```json
{
  "ip_address": "10.0.0.10",
  "hostname": "node-a",
  "service_name": "checkout",
  "agent_version": "1.2.3",
  "run_mode": "k8s",
  "namespace": "observability",
  "pod_name": "gprofiler-abcde",
  "containers": [
    {
      "container_id": "abc123",
      "container_name": "checkout",
      "runtime": "containerd",
      "namespace": "shop",
      "pod_name": "checkout-7f8d9",
      "workload_name": "checkout",
      "workload_kind": "k8s",
      "processes": [
        {
          "pid": 1234,
          "process_name": "java"
        }
      ]
    }
  ]
}
```

## Metadata Discovery Rules

The agent should use the following sources in order of confidence:

1. container runtime inventory from `granulate_utils.containers.client`
2. runtime labels such as:
   - `io.kubernetes.pod.namespace`
   - `io.kubernetes.pod.name`
   - `io.kubernetes.container.name`
3. process-to-container resolution via cgroup/container-id lookup
4. environment metadata for the agent pod itself, such as `POD_NAMESPACE` and
   `POD_NAME`

If no container runtime is available, the agent must keep heartbeat delivery
working and omit workload inventory rather than failing the control plane.

## Workload Name Inference

The agent may infer a workload name using:

- `app.kubernetes.io/name`
- `app`
- pod-name normalization for ReplicaSet- and StatefulSet-shaped pod names

This inference is best-effort only. The backend must treat these values as
helpful selectors, not as immutable workload identifiers.

## Command-Execution Model

The agent does not change how profiling commands are executed:

- host-level selections remain host-scoped commands
- workload-level selections are backend-resolved into host/PID mappings
- process profiling still uses existing `pids_to_profile` behavior

This intentionally avoids introducing a second targeting model inside the agent.

## Failure Handling

The agent must:

- continue sending heartbeats if workload discovery fails
- log discovery failures as diagnostic information, not fatal errors
- avoid blocking command receipt on container/runtime metadata issues
- publish empty `containers` when inventory cannot be collected

## Compatibility

This design preserves compatibility with existing backends because:

- all new heartbeat fields are additive
- the backend may ignore workload metadata safely
- command delivery and completion flows are unchanged

## Acceptance Tests

These acceptance criteria define "done" for the agent side of workload-level
profiling. They are written as Given/When/Then so they can drive spec-first
development and be implemented as automated unit/integration tests.

### Workload inventory reporting

- **AT-A1 — Inventory attached when runtime available.** *Given* a supported
  container runtime is present, *When* the agent builds a heartbeat, *Then* the
  payload includes `containers[]` where each entry carries container identity,
  best-effort `namespace`/`pod_name`/`workload_name`/`workload_kind`, and the
  `processes[]` (pid + process_name) running in that container.
- **AT-A2 — No runtime is graceful.** *Given* no container runtime is available,
  *When* the heartbeat is built, *Then* `containers` is empty and the heartbeat
  is still sent successfully (control plane is never blocked).
- **AT-A3 — Discovery failure is non-fatal.** *Given* workload discovery raises
  an error, *When* the heartbeat is built, *Then* the failure is logged as
  diagnostic (not fatal), `containers` is published empty, and both heartbeat
  delivery and command receipt continue.
- **AT-A4 — Workload-name inference.** *Given* pod labels and/or a
  ReplicaSet/StatefulSet-shaped pod name, *When* inferring a workload name,
  *Then* the agent uses `app.kubernetes.io/name`, then `app`, then pod-name
  normalization, treating the result as a best-effort selector only.
- **AT-A5 — Agent-pod env fallback.** *Given* `POD_NAMESPACE`/`POD_NAME` are set
  and richer sources are unavailable, *When* building metadata for the agent's
  own pod, *Then* those env values are used as fallback.

### Command execution (unchanged model)

- **AT-A6 — Host-level command.** *Given* a host-scoped command for
  `(hostname, service_name)`, *When* executed, *Then* the agent profiles
  host-wide exactly as before workload support.
- **AT-A7 — PID-level command.** *Given* a command whose config contains
  backend-resolved `pids`, *When* executed, *Then* only those PIDs are profiled
  via the existing `pids_to_profile` path; no second targeting model is
  introduced in the agent.
- **AT-A8 — Completion reporting.** *Given* a command finishes (success or
  failure), *When* the agent reports back, *Then* it uses the existing
  completion control plane keyed by `command_id`.

### Compatibility

- **AT-A9 — Additive heartbeat fields.** *Given* a backend that predates workload
  support, *When* it receives the enriched heartbeat, *Then* it can ignore the
  new fields without error and host-based behavior is unaffected.

## Spec-Driven Development Workflow

Future workload-related changes in this repo should follow this sequence:

1. Update this spec first.
2. Describe any heartbeat contract changes explicitly.
3. Document compatibility and rollout behavior.
4. Implement code only after the spec reflects the intended design.
5. Keep the implementation aligned with `.claude/skills/implement-from-spec`.

## Future Extensions

Potential follow-ups that should begin as spec changes:

- stable workload identifiers beyond best-effort names
- richer workload kinds such as `deployment`, `daemonset`, `job`, and `cronjob`
- container-image metadata for targeting/debugging
- workload-aware continuous retargeting when pod membership changes
