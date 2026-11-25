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
Tests for the PerfSpect ext metrics in gprofiler/hw_metrics.py
"""

import logging
from pathlib import Path
from typing import List

import pytest
from pytest import LogCaptureFixture

from gprofiler.hw_metrics import NoopHWMetricsMonitor, PerfSpectRawCsvReader

# The raw CSV as PerfSpect's printMetricsCSV writes it at system scope: TS is unix seconds, SKT/CPU/CID are empty,
# and NaN values are empty fields. Non-allowlisted columns are mixed in, as in a real header.
HEADER = (
    "TS,SKT,CPU,CID,CPU operating frequency (in GHz),CPU utilization %,CPI,kernel_CPI,"
    "TMA_Frontend_Bound(%),TMA_Bad_Speculation(%),TMA_Backend_Bound(%),TMA_Retiring(%)\n"
)
CWF_HEADER = "TS,SKT,CPU,CID,CPU operating frequency (in GHz),CPU utilization %,CPI,kernel_CPI\n"


def row(ts: int, freq: str, cpi: str, frontend: str = "20") -> str:
    # bad_spec, backend and retiring are fixed at 5, 45 and 30
    return f"{ts},,,,{freq},50.5,{cpi},1.5,{frontend},5,45,30\n"


def append(path: Path, *lines: str) -> None:
    with path.open("a") as f:
        f.write("".join(lines))


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    return tmp_path / "host_metrics.csv"


def test_mean_of_allowlisted_columns(csv_path: Path) -> None:
    append(csv_path, HEADER, row(100, "2.0", "1.0"), row(105, "3.0", "2.0", frontend="30"))
    assert PerfSpectRawCsvReader(str(csv_path)).collect() == {
        "perfspect.cpu_freq_ghz": 2.5,
        "perfspect.cpi": 1.5,
        "perfspect.tma.frontend": 25.0,
        "perfspect.tma.bad_spec": 5.0,
        "perfspect.tma.backend": 45.0,
        "perfspect.tma.retiring": 30.0,
    }


def test_only_rows_since_the_last_call(csv_path: Path) -> None:
    reader = PerfSpectRawCsvReader(str(csv_path))
    append(csv_path, HEADER, row(100, "2.0", "1.0"))
    assert reader.collect()["perfspect.cpi"] == 1.0

    append(csv_path, row(105, "3.0", "4.0"), row(110, "3.0", "6.0"))
    assert reader.collect()["perfspect.cpi"] == 5.0

    # PerfSpect has stopped collecting: nothing new, so nothing is sent
    assert reader.collect() == {}


def test_partial_trailing_line_is_left_for_the_next_call(csv_path: Path) -> None:
    reader = PerfSpectRawCsvReader(str(csv_path))
    full = row(105, "3.0", "8.0")
    append(csv_path, HEADER, row(100, "2.0", "2.0"), full[:12])
    assert reader.collect()["perfspect.cpi"] == 2.0

    append(csv_path, full[12:])
    assert reader.collect()["perfspect.cpi"] == 8.0


def test_partial_header_is_left_for_the_next_call(csv_path: Path) -> None:
    reader = PerfSpectRawCsvReader(str(csv_path))
    append(csv_path, HEADER[:20])
    assert reader.collect() == {}

    append(csv_path, HEADER[20:], row(100, "2.0", "3.0"))
    assert reader.collect()["perfspect.cpi"] == 3.0


def test_empty_fields_are_skipped(csv_path: Path) -> None:
    append(csv_path, HEADER, row(100, "2.0", ""), row(105, "4.0", "3.0"), row(110, "", "", frontend=""))
    ext = PerfSpectRawCsvReader(str(csv_path)).collect()
    assert ext["perfspect.cpi"] == 3.0
    assert ext["perfspect.cpu_freq_ghz"] == 3.0
    assert ext["perfspect.tma.frontend"] == 20.0


def test_non_finite_values_are_skipped(csv_path: Path) -> None:
    # Go's FormatFloat writes +Inf/-Inf as is (only NaN becomes an empty field)
    append(csv_path, HEADER, row(100, "2.0", "+Inf"), row(105, "2.0", "3.0"), row(110, "2.0", "-Inf"))
    assert PerfSpectRawCsvReader(str(csv_path)).collect()["perfspect.cpi"] == 3.0


def test_metric_without_any_value_is_absent(csv_path: Path) -> None:
    append(csv_path, HEADER, row(100, "2.0", ""), row(105, "2.0", ""))
    assert "perfspect.cpi" not in PerfSpectRawCsvReader(str(csv_path)).collect()


def test_missing_columns_are_absent(csv_path: Path) -> None:
    # CWF has no TMA metrics
    append(csv_path, CWF_HEADER, "100,,,,2.5,50.5,1.25,1.5\n")
    assert PerfSpectRawCsvReader(str(csv_path)).collect() == {"perfspect.cpu_freq_ghz": 2.5, "perfspect.cpi": 1.25}


def test_missing_file_then_created(csv_path: Path) -> None:
    reader = PerfSpectRawCsvReader(str(csv_path))
    assert reader.collect() == {}

    append(csv_path, HEADER, row(100, "2.0", "1.0"))
    assert reader.collect()["perfspect.cpi"] == 1.0


def test_recreated_file_resets_the_reader(csv_path: Path) -> None:
    reader = PerfSpectRawCsvReader(str(csv_path))
    append(csv_path, HEADER, row(100, "2.0", "1.0"), row(105, "2.0", "1.0"))
    reader.collect()

    csv_path.unlink()
    append(csv_path, CWF_HEADER, "200,,,,2.5,50.5,7.0,1.5\n")
    assert reader.collect() == {"perfspect.cpu_freq_ghz": 2.5, "perfspect.cpi": 7.0}


def test_symlink_is_refused(tmp_path: Path, csv_path: Path, caplog: LogCaptureFixture) -> None:
    target = tmp_path / "elsewhere.csv"
    append(target, HEADER, row(100, "2.0", "1.0"))
    csv_path.symlink_to(target)

    with caplog.at_level(logging.WARNING):
        assert PerfSpectRawCsvReader(str(csv_path)).collect() == {}
    assert "Can't read PerfSpect metrics" in caplog.text


@pytest.mark.parametrize("lines", [[], [HEADER]])
def test_no_rows_yet(csv_path: Path, lines: List[str]) -> None:
    csv_path.write_text("".join(lines))
    assert PerfSpectRawCsvReader(str(csv_path)).collect() == {}


def test_noop_monitor_has_no_ext_metrics() -> None:
    assert NoopHWMetricsMonitor().get_hw_metrics().ext == {}
