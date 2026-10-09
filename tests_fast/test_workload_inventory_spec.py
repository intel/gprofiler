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

"""
Fast acceptance tests for agent heartbeat workload inventory.

Covers WORKLOAD_LEVEL_PROFILING_SPEC.md (gProfiler side):

* AT-A1 — inventory attached when a runtime is available
* AT-A2 — no runtime is graceful (empty containers, heartbeat still built)
* AT-A3 — discovery failure is non-fatal (logged, empty containers)
* AT-A4 — best-effort workload-name inference (labels > pod-name normalization)
* AT-A5 — agent-pod env fallback (POD_NAMESPACE / POD_NAME)

``gprofiler.metadata.heartbeat_metadata`` normally imports the full
``granulate_utils`` container stack (grpc/docker) plus glogger. Those are agent
runtime concerns, not what this spec is about, so we load the module in
isolation with light stubs for its heavy imports. This keeps the suite instant
and dependency-free while still exercising the real inventory-building code.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("psutil", reason="psutil is required to build heartbeat inventory")


class _StubContainersClient:
    """Placeholder; tests set collector._containers_client directly."""


class _StubNoContainerRuntimesError(Exception):
    pass


def _install_stub_modules():
    """Register lightweight stand-ins for heartbeat_metadata's heavy imports."""

    def _mod(name, **attrs):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module
        return module

    # granulate_utils.* container stack (grpc/docker) -> stubs
    _mod("granulate_utils")
    _mod("granulate_utils.containers")
    _mod("granulate_utils.containers.client", ContainersClient=_StubContainersClient)
    _mod("granulate_utils.exceptions", NoContainerRuntimesError=_StubNoContainerRuntimesError)
    _mod("granulate_utils.linux")
    _mod("granulate_utils.linux.containers", get_process_container_id=lambda proc: None)

    # gprofiler.log needs glogger; gprofiler.metadata.system_metadata needs
    # granulate_utils. Stub just the symbols heartbeat_metadata imports.
    import logging

    _mod("gprofiler.log", get_logger_adapter=lambda name: logging.getLogger(name))
    _mod("gprofiler.metadata.system_metadata", get_run_mode=lambda: "k8s")


def _load_heartbeat_metadata():
    _install_stub_modules()
    module_path = Path(__file__).resolve().parents[1] / "gprofiler" / "metadata" / "heartbeat_metadata.py"
    spec = importlib.util.spec_from_file_location("heartbeat_metadata_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hb = _load_heartbeat_metadata()


# ---------------------------------------------------------------------------
# AT-A4 — workload-name inference
# ---------------------------------------------------------------------------


class TestWorkloadNameInferenceSpec:
    def test_app_kubernetes_io_name_label_wins(self):
        name = hb._best_effort_workload_name(
            "web-5d4b8c7f9c-abcde",
            {"app.kubernetes.io/name": "checkout", "app": "other"},
        )
        assert name == "checkout"

    def test_app_label_is_second_choice(self):
        assert hb._best_effort_workload_name("web-5d4b8c7f9c-tl6qw", {"app": "cart"}) == "cart"

    def test_vendor_crd_name_label_is_used_when_configured(self):
        # A deployment can configure vendor/CRD label keys; they are probed first and
        # win over pod-name normalization. Here the label value differs from the
        # normalized pod name to prove the label (not the regex) produced the result.
        assert (
            hb._best_effort_workload_name(
                "validation-85ff989f55-tl6qw",
                {"pinterest.com/crd_name": "billing-validation"},
                None,
                ("pinterest.com/crd_name",) + hb.DEFAULT_WORKLOAD_NAME_LABELS,
            )
            == "billing-validation"
        )

    def test_unconfigured_vendor_name_label_is_ignored(self):
        # With only the vendor-neutral defaults, a vendor key is not consulted, so we
        # fall back to pod-name normalization.
        assert (
            hb._best_effort_workload_name("validation-85ff989f55-tl6qw", {"pinterest.com/crd_name": "billing"})
            == "validation"
        )

    def test_placeholder_label_values_are_ignored(self):
        assert (
            hb._best_effort_workload_name(
                "kube-proxy-node1",
                {"pinterest.com/crd_name": "unknown"},
                None,
                ("pinterest.com/crd_name",),
            )
            == "kube-proxy-node1"
        )

    def test_replicaset_pod_name_is_normalized(self):
        assert hb._best_effort_workload_name("web-5d4b8c7f9c-tl6qw", {}) == "web"

    def test_statefulset_pod_name_is_normalized(self):
        assert hb._best_effort_workload_name("postgres-0", {}) == "postgres"

    def test_daemonset_pod_name_is_normalized(self):
        assert hb._best_effort_workload_name("metrics-agent-lvpdm", {}) == "metrics-agent"

    def test_standalone_pod_name_with_random_suffix_is_normalized(self):
        assert hb._best_effort_workload_name("visibilitymetrics-pinapp-test-0", {}) == "visibilitymetrics-pinapp-test"

    def test_name_tail_with_vowels_is_not_stripped(self):
        # "redis" contains vowels, so it is not a k8s-generated suffix.
        assert hb._best_effort_workload_name("service-redis", {}) == "service-redis"

    def test_plain_pod_name_is_returned_as_is(self):
        assert hb._best_effort_workload_name("standalone", {}) == "standalone"

    def test_no_pod_name_and_no_labels_is_none(self):
        assert hb._best_effort_workload_name(None, {}) is None

    def test_pod_sandbox_labels_are_preferred_over_container_labels(self):
        # EKS-style node: container labels are empty, the workload name lives on the
        # pod sandbox. The sandbox source must win over pod-name normalization.
        assert (
            hb._best_effort_workload_name(
                "cronjobcontroller-7bf9b-zhs2o",
                {},
                {"pinterest.com/crd_name": "cronjobcontroller"},
                ("pinterest.com/crd_name",),
            )
            == "cronjobcontroller"
        )

    def test_pod_sandbox_labels_win_over_container_labels_when_both_present(self):
        assert (
            hb._best_effort_workload_name(
                "web-5d4b8c7f9c-tl6qw",
                {"app": "container-level"},
                {"app.kubernetes.io/name": "sandbox-level"},
            )
            == "sandbox-level"
        )

    def test_container_labels_used_when_sandbox_labels_absent(self):
        assert hb._best_effort_workload_name("web-5d4b8c7f9c-tl6qw", {"app": "cart"}, {}) == "cart"

    def test_pod_name_normalization_when_no_labels_anywhere(self):
        assert hb._best_effort_workload_name("metrics-agent-lvpdm", {}, {}) == "metrics-agent"


class TestWorkloadKindInferenceSpec:
    def test_vendor_crd_type_label_wins_when_configured(self):
        assert (
            hb._best_effort_workload_kind(
                "metrics-agent-lvpdm",
                {"pinterest.com/crd_type": "PinterestDaemon"},
                None,
                ("pinterest.com/crd_type",),
            )
            == "PinterestDaemon"
        )

    def test_unconfigured_vendor_kind_label_is_ignored(self):
        # With no configured kind labels, the vendor key is not consulted; the kind is
        # inferred from the pod-name shape instead.
        assert (
            hb._best_effort_workload_kind("metrics-agent-lvpdm", {"pinterest.com/crd_type": "PinterestDaemon"})
            == "DaemonSet"
        )

    def test_vendor_crd_type_from_pod_sandbox_labels(self):
        assert (
            hb._best_effort_workload_kind(
                "cronjobcontroller-7bf9b-zhs2o",
                {},
                {"pinterest.com/crd_type": "PinApp"},
                ("pinterest.com/crd_type",),
            )
            == "PinApp"
        )

    def test_placeholder_crd_type_falls_through_to_inference(self):
        assert (
            hb._best_effort_workload_kind(
                "metrics-agent-lvpdm",
                {"pinterest.com/crd_type": "unknown"},
                None,
                ("pinterest.com/crd_type",),
            )
            == "DaemonSet"
        )

    def test_replicaset_pod_name_infers_deployment(self):
        assert hb._best_effort_workload_kind("web-5d4b8c7f9c-tl6qw", {}) == "Deployment"

    def test_statefulset_pod_name_infers_statefulset(self):
        assert hb._best_effort_workload_kind("postgres-0", {}) == "StatefulSet"

    def test_daemonset_pod_name_infers_daemonset(self):
        assert hb._best_effort_workload_kind("metrics-agent-lvpdm", {}) == "DaemonSet"

    def test_k8s_pod_without_recognizable_shape_is_k8s(self):
        assert hb._best_effort_workload_kind("standalone", {"io.kubernetes.pod.namespace": "shop"}) == "k8s"

    def test_non_k8s_container_is_container_kind(self):
        assert hb._best_effort_workload_kind(None, {}) == "container"


# ---------------------------------------------------------------------------
# Collector behavior (AT-A1/AT-A2/AT-A3/AT-A5)
# ---------------------------------------------------------------------------


@pytest.fixture
def collector():
    # __init__ tries to build a real ContainersClient (our stub); tests then set
    # _containers_client explicitly to control the runtime scenario.
    inst = object.__new__(hb.HeartbeatMetadataCollector)
    inst._refresh_interval_seconds = 0
    inst._last_snapshot_at = 0.0
    inst._last_snapshot = {"containers": []}
    inst._containers_client = None
    inst._workload_name_labels = hb.DEFAULT_WORKLOAD_NAME_LABELS
    inst._workload_kind_labels = hb.DEFAULT_WORKLOAD_KIND_LABELS
    return inst


class _FakeContainer:
    def __init__(self, id, name, runtime, labels, pod_labels=None):
        self.id = id
        self.name = name
        self.runtime = runtime
        self.labels = labels
        self.pod_labels = pod_labels or {}


class _FakeProc:
    def __init__(self, pid, name):
        self.pid = pid
        self.info = {"pid": pid, "name": name}


class TestNoRuntimeSpec:
    def test_no_runtime_yields_empty_inventory(self, collector):
        # AT-A2
        collector._containers_client = None
        assert collector._collect_containers() == []

    def test_collect_still_builds_a_heartbeat_snapshot(self, collector, monkeypatch):
        # AT-A2: control plane never blocked; snapshot is well-formed.
        monkeypatch.setenv("POD_NAMESPACE", "obs")
        monkeypatch.setenv("POD_NAME", "gprofiler-x")
        snapshot = collector.collect()
        assert snapshot["containers"] == []
        assert "agent_version" in snapshot
        assert snapshot["run_mode"] == "k8s"


class TestDiscoveryFailureSpec:
    def test_list_containers_error_is_non_fatal(self, collector):
        # AT-A3
        class _Boom:
            def list_containers(self):
                raise RuntimeError("runtime exploded")

        collector._containers_client = _Boom()
        assert collector._collect_containers() == []


class TestEnvFallbackSpec:
    def test_pod_namespace_and_name_come_from_env(self, collector, monkeypatch):
        # AT-A5
        monkeypatch.setenv("POD_NAMESPACE", "observability")
        monkeypatch.setenv("POD_NAME", "gprofiler-abcde")
        snapshot = collector.collect()
        assert snapshot["namespace"] == "observability"
        assert snapshot["pod_name"] == "gprofiler-abcde"


class TestInventoryAttachedSpec:
    def test_inventory_carries_identity_metadata_and_processes(self, collector, monkeypatch):
        # AT-A1: with a runtime, each container entry has identity, best-effort
        # k8s metadata, and its process list (pid + process_name).
        container = _FakeContainer(
            id="c1",
            name="checkout",
            runtime="containerd",
            labels={
                "io.kubernetes.pod.namespace": "shop",
                "io.kubernetes.pod.name": "checkout-7f8d9",
                "io.kubernetes.container.name": "checkout",
                "app.kubernetes.io/name": "checkout",
            },
        )

        class _Client:
            def list_containers(self):
                return [container]

        collector._containers_client = _Client()
        monkeypatch.setattr(hb, "Process", lambda pid: pid, raising=True)
        monkeypatch.setattr(hb, "get_process_container_id", lambda proc: "c1", raising=True)
        monkeypatch.setattr(hb, "process_iter", lambda fields: [_FakeProc(1234, "java"), _FakeProc(20, "sh")])

        inventory = collector._collect_containers()
        assert len(inventory) == 1
        entry = inventory[0]
        assert entry["container_id"] == "c1"
        assert entry["container_name"] == "checkout"
        assert entry["namespace"] == "shop"
        assert entry["pod_name"] == "checkout-7f8d9"
        assert entry["workload_name"] == "checkout"
        assert entry["workload_kind"] == "DaemonSet"
        # processes are sorted by pid
        assert entry["processes"] == [
            {"pid": 20, "process_name": "sh"},
            {"pid": 1234, "process_name": "java"},
        ]

    def test_workload_name_resolved_from_pod_sandbox_labels(self, collector, monkeypatch):
        # AT-A4: EKS-style node where container labels lack workload identity but the
        # pod sandbox carries it under configured vendor keys. Inventory must surface
        # the sandbox-derived name and kind.
        collector._workload_name_labels = ("pinterest.com/crd_name",) + hb.DEFAULT_WORKLOAD_NAME_LABELS
        collector._workload_kind_labels = ("pinterest.com/crd_type",) + hb.DEFAULT_WORKLOAD_KIND_LABELS
        container = _FakeContainer(
            id="c2",
            name="cronjobcontroller",
            runtime="containerd",
            labels={
                "io.kubernetes.pod.namespace": "kube-system",
                "io.kubernetes.pod.name": "cronjobcontroller-7bf9b-zhs2o",
                "io.kubernetes.container.name": "cronjobcontroller",
            },
            pod_labels={"pinterest.com/crd_name": "cronjobcontroller", "pinterest.com/crd_type": "PinApp"},
        )

        class _Client:
            def list_containers(self):
                return [container]

        collector._containers_client = _Client()
        monkeypatch.setattr(hb, "Process", lambda pid: pid, raising=True)
        monkeypatch.setattr(hb, "get_process_container_id", lambda proc: None, raising=True)
        monkeypatch.setattr(hb, "process_iter", lambda fields: [])

        entry = collector._collect_containers()[0]
        assert entry["pod_name"] == "cronjobcontroller-7bf9b-zhs2o"
        assert entry["workload_name"] == "cronjobcontroller"
        assert entry["workload_kind"] == "PinApp"

    def test_non_k8s_container_is_labeled_container_kind(self, collector, monkeypatch):
        container = _FakeContainer(id="d1", name="redis", runtime="docker", labels={})

        class _Client:
            def list_containers(self):
                return [container]

        collector._containers_client = _Client()
        monkeypatch.setattr(hb, "Process", lambda pid: pid, raising=True)
        monkeypatch.setattr(hb, "get_process_container_id", lambda proc: None, raising=True)
        monkeypatch.setattr(hb, "process_iter", lambda fields: [])

        entry = collector._collect_containers()[0]
        assert entry["workload_kind"] == "container"
        assert entry["namespace"] is None
        assert entry["processes"] == []
