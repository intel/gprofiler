import base64
import gzip
import math
import os
import platform
import subprocess  # nosec B404
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Dict, Optional

from gprofiler.log import get_logger_adapter
from gprofiler.utils.fs import mkdir_owned_root, safe_copy

logger = get_logger_adapter(__name__)

DEFAULT_POLLING_INTERVAL_SECONDS = 5
STOP_TIMEOUT_SECONDS = 2
PERFSPECT_DATA_DIRECTORY = "/tmp/perfspect_data"

PERFSPECT_EXT_METRICS = {
    "CPI": "perfspect.cpi",
    "CPU operating frequency (in GHz)": "perfspect.cpu_freq_ghz",
    "TMA_Frontend_Bound(%)": "perfspect.tma.frontend",
    "TMA_Bad_Speculation(%)": "perfspect.tma.bad_spec",
    "TMA_Backend_Bound(%)": "perfspect.tma.backend",
    "TMA_Retiring(%)": "perfspect.tma.retiring",
}


@dataclass
class HWMetrics:
    # HW metrics data in json format
    metrics_data: Optional[dict]
    # base64 encoded HTML data
    metrics_html: Optional[str]
    # PerfSpect metrics averaged over this cycle by ext metric name
    ext: Dict[str, float] = field(default_factory=dict)


class PerfSpectRawCsvReader:
    """
    Reads the rows PerfSpect appended to its raw metrics CSV since the last call (one row per interval)
    and returns the mean of each metric over them.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._offset = 0
        # ext metric name -> column index, set once the header line has been read
        self._columns: Optional[Dict[str, int]] = None

    def collect(self) -> Dict[str, float]:
        try:
            fd = os.open(self._path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return {}
        except OSError as e:
            logger.warning(f"Can't read PerfSpect metrics from {self._path}: {e!r}")
            return {}
        with os.fdopen(fd, "rb") as f:
            if os.fstat(f.fileno()).st_size < self._offset:
                self._offset, self._columns = 0, None  # the file was recreated
            f.seek(self._offset)
            data = f.read()

        # PerfSpect appends while we read, so leave a trailing partial line for the next call
        complete = data[: data.rfind(b"\n") + 1]
        self._offset += len(complete)

        sums: Dict[str, float] = {}
        counts: Dict[str, int] = {}
        for line in complete.decode(errors="replace").splitlines():
            fields = line.split(",")
            if self._columns is None:
                self._columns = {
                    PERFSPECT_EXT_METRICS[n]: i for i, n in enumerate(fields) if n in PERFSPECT_EXT_METRICS
                }
                continue
            for name, i in self._columns.items():
                if i >= len(fields) or fields[i] == "":
                    continue
                try:
                    value = float(fields[i])
                except ValueError:
                    continue
                if not math.isfinite(value):
                    continue
                sums[name] = sums.get(name, 0.0) + value
                counts[name] = counts.get(name, 0) + 1

        return {name: sums[name] / counts[name] for name in sums}


class HWMetricsMonitorBase(metaclass=ABCMeta):
    @abstractmethod
    def start(self) -> None:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    @abstractmethod
    def _get_hw_metrics_dict(self) -> Optional[dict]:
        """
        Returns the HW metrics in dictionary data structure
        """
        raise NotImplementedError

    @abstractmethod
    def _get_hw_metrics_html(self) -> Optional[str]:
        """
        Returns the base64 encoded string with HW metrics in HTML format
        """
        raise NotImplementedError

    def _get_hw_ext_metrics(self) -> Dict[str, float]:
        """
        Returns the PerfSpect metrics for this cycle, by ext metric name
        """
        return {}

    def get_hw_metrics(self) -> HWMetrics:
        return HWMetrics(self._get_hw_metrics_dict(), self._get_hw_metrics_html(), self._get_hw_ext_metrics())


class HWMetricsMonitor(HWMetricsMonitorBase):
    def __init__(
        self,
        stop_event: Event,
        perfspect_path: Optional[Path] = None,
        perfspect_duration: int = 60,
        polling_rate_seconds: int = DEFAULT_POLLING_INTERVAL_SECONDS,
        verbose: bool = False,
    ):
        self._polling_rate_seconds = polling_rate_seconds
        self._stop_event = stop_event
        self._thread: Optional[Thread] = None
        self._lock = RLock()
        self._ps_process: Optional[subprocess.Popen[bytes]] = None
        self._perfspect_path: Optional[Path] = perfspect_path
        self._perfspect_duration = perfspect_duration
        self._verbose = verbose

        self._ps_raw_csv_filename = PERFSPECT_DATA_DIRECTORY + "/" + platform.node() + "_metrics.csv"
        self._ps_summary_csv_filename = PERFSPECT_DATA_DIRECTORY + "/" + platform.node() + "_metrics_summary.csv"
        self._ps_summary_html_filename = PERFSPECT_DATA_DIRECTORY + "/" + platform.node() + "_metrics_summary.html"
        self._ps_latest_csv_filename = PERFSPECT_DATA_DIRECTORY + "/" + platform.node() + "_metrics_summary_latest.csv"
        self._ps_latest_html_filename = (
            PERFSPECT_DATA_DIRECTORY + "/" + platform.node() + "_metrics_summary_latest.html"
        )

        self._cleanup()
        self._raw_csv_reader = PerfSpectRawCsvReader(self._ps_raw_csv_filename)

    def start(self) -> None:
        if (
            self._perfspect_path is None
            or not os.path.isfile(self._perfspect_path)
            or not os.access(self._perfspect_path, os.X_OK)
        ):
            return None

        ps_cmd = [
            str(self._perfspect_path),
            "metrics",
            "--duration",
            str(self._perfspect_duration),
            "--output",
            PERFSPECT_DATA_DIRECTORY,
        ]

        # Add --debug if verbose is enabled
        if self._verbose:
            ps_cmd.append("--debug")

        self._ps_process = subprocess.Popen(ps_cmd, stdout=subprocess.PIPE)
        Thread(target=self._reap_ps_process, daemon=True).start()

    def _reap_ps_process(self) -> None:
        if self._ps_process is not None:
            self._ps_process.wait()
            self._ps_process = None

    def stop(self) -> None:
        if self._ps_process:
            self._ps_process.terminate()

        self._cleanup()
        self._thread = None

    def _safe_remove(self, path: str) -> None:
        """
        Safely remove a file, refusing to follow symlinks.

        Security: Checks if path is a symlink before removal.
        If path is a symlink, raises an exception instead of removing the target.
        """
        if os.path.islink(path):
            raise Exception(f"Refusing to remove {path}: symlinks are not allowed for security reasons")
        if os.path.exists(path):
            os.remove(path)

    def _cleanup(self) -> None:
        # Ensure the directory exists and is owned by root.
        # mkdir_owned_root() will remove and recreate the directory if it exists
        # but is not owned by root, protecting against pre-created attacker directories.
        mkdir_owned_root(PERFSPECT_DATA_DIRECTORY)

        # Remove old data files using safe removal (rejects symlinks)
        self._safe_remove(self._ps_raw_csv_filename)
        self._safe_remove(self._ps_summary_csv_filename)
        self._safe_remove(self._ps_summary_html_filename)
        self._safe_remove(self._ps_latest_csv_filename)
        self._safe_remove(self._ps_latest_html_filename)

    def _get_hw_metrics_dict(self) -> Optional[dict]:
        summary_dict = {}
        if os.path.exists(self._ps_summary_csv_filename) and os.path.isfile(self._ps_summary_csv_filename):
            safe_copy(self._ps_summary_csv_filename, self._ps_latest_csv_filename)
            with open(self._ps_latest_csv_filename, "r") as f:
                next(f)  # Skip the first line
                for line in f:
                    csv_data = line.split(",")
                    summary_dict[csv_data[0]] = csv_data[1]

            self._safe_remove(self._ps_latest_csv_filename)
            return summary_dict

        else:
            return None

    def _get_hw_ext_metrics(self) -> Dict[str, float]:
        return self._raw_csv_reader.collect()

    def _get_hw_metrics_html(self) -> Optional[str]:
        if os.path.exists(self._ps_summary_html_filename) and os.path.isfile(self._ps_summary_html_filename):
            encoded_html_data = None
            safe_copy(self._ps_summary_html_filename, self._ps_latest_html_filename)
            with open(self._ps_latest_html_filename, "rb") as f:
                html_data = f.read()
                # Compress the HTML data using gzip
                compressed_html_data = gzip.compress(html_data)

                # For debug, save the compressed HTML data to a file
                # compressed_html_filename = self._ps_latest_html_filename + ".gz"
                # with open(compressed_html_filename, "wb") as compressed_html_file:
                #     compressed_html_file.write(compressed_html_data)
                #     compressed_html_file.close()

                # Encode the compressed HTML data to base64
                encoded_html_data = base64.b64encode(compressed_html_data).decode("utf-8")

                # For debug, save the base64 encoded HTML data to a file
                # encoded_html_filename = self._ps_latest_html_filename + ".b64"
                # with open(encoded_html_filename, "w") as encoded_html_file:
                #     encoded_html_file.write(encoded_html_data)
                # encoded_html_file.close()

            self._safe_remove(self._ps_latest_html_filename)
            return encoded_html_data

        else:
            return None


class NoopHWMetricsMonitor(HWMetricsMonitorBase):
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def _get_hw_metrics_dict(self) -> Optional[dict]:
        return None

    def _get_hw_metrics_html(self) -> Optional[str]:
        return None
