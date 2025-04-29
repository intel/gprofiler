import base64
import gzip
import os
import platform
import shutil
import subprocess
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Optional

DEFAULT_POLLING_INTERVAL_SECONDS = 5
STOP_TIMEOUT_SECONDS = 2
PERFSPECT_DATA_DIRECTORY = "/tmp/perfspect_data"


@dataclass
class HWMetrics:
    # HW metrics data in json format
    metrics_data: Optional[dict]
    # base64 encoded HTML data
    metrics_html: Optional[str]


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

    def get_hw_metrics(self) -> HWMetrics:
        return HWMetrics(self._get_hw_metrics_dict(), self._get_hw_metrics_html())


class HWMetricsMonitor(HWMetricsMonitorBase):
    def __init__(
        self,
        stop_event: Event,
        perfspect_path: Optional[Path] = None,
        perfspect_duration: int = 60,
        polling_rate_seconds: int = DEFAULT_POLLING_INTERVAL_SECONDS,
    ):
        self._polling_rate_seconds = polling_rate_seconds
        self._stop_event = stop_event
        self._thread: Optional[Thread] = None
        self._lock = RLock()
        self._ps_process: Optional[subprocess.Popen[bytes]] = None
        self._perfspect_path: Optional[Path] = perfspect_path
        self._perfspect_duration = perfspect_duration

        self._ps_raw_csv_filename = PERFSPECT_DATA_DIRECTORY + "/" + platform.node() + "_metrics.csv"
        self._ps_summary_csv_filename = PERFSPECT_DATA_DIRECTORY + "/" + platform.node() + "_metrics_summary.csv"
        self._ps_summary_html_filename = PERFSPECT_DATA_DIRECTORY + "/" + platform.node() + "_metrics_summary.html"
        self._ps_latest_csv_filename = PERFSPECT_DATA_DIRECTORY + "/" + platform.node() + "_metrics_summary_latest.csv"
        self._ps_latest_html_filename = (
            PERFSPECT_DATA_DIRECTORY + "/" + platform.node() + "_metrics_summary_latest.html"
        )

        if not os.path.exists(PERFSPECT_DATA_DIRECTORY):
            os.makedirs(PERFSPECT_DATA_DIRECTORY)
        else:
            if os.path.exists(self._ps_raw_csv_filename):
                os.remove(self._ps_raw_csv_filename)
            if os.path.exists(self._ps_summary_csv_filename):
                os.remove(self._ps_summary_csv_filename)
            if os.path.exists(self._ps_summary_html_filename):
                os.remove(self._ps_summary_html_filename)

    def start(self) -> None:
        # assert self._thread is None, "HWMetricsMonitor is already running"
        # assert not self._stop_event.is_set(), "Stop condition is already set (perhaps gProfiler was already stopped?)"

        if self._perfspect_path is None:
            return None

        ps_cmd = [
            str(self._perfspect_path),
            "metrics",
            # "--metrics",
            # '"CPU operating frequency (in GHz)","CPI","TMA_Frontend_Bound(%)","TMA_Bad_Speculation(%)",'
            # '"TMA_Backend_Bound(%)","TMA_Retiring(%)"',
            "--duration",
            str(self._perfspect_duration),
            # "--live",
            # "--format",
            # "csv",
            # "--interval",
            # "10",
            "--output",
            PERFSPECT_DATA_DIRECTORY,
        ]

        self._ps_process = subprocess.Popen(ps_cmd, stdout=subprocess.PIPE)
        #    ps_stdout, ps_stderr = ps_process.communicate()
        #    try:
        #        # wait 2 seconds to ensure it starts
        #        ps_process.wait(2)
        #    except subprocess.TimeoutExpired:
        #        pass
        #    else:
        #        raise Exception(f"Command {ps_cmd} exited unexpectedly with {ps_process.returncode}")
        # self._thread = Thread(target=self._continuously_poll_perfspect, args=(self._polling_rate_seconds,))
        # self._thread.start()

    def stop(self) -> None:
        # assert self._thread is not None, "HWMetricsMonitor is not running"
        # assert self._stop_event.is_set(), "Stop event was not set before stopping the HWMetricsMonitor"
        if self._ps_process:
            self._ps_process.terminate()
        # self._thread.join(STOP_TIMEOUT_SECONDS)
        # if self._thread.is_alive():
        # raise ThreadStopTimeoutError("Timed out while waiting for the HWMetricsMonitor internal thread to stop")
        self._thread = None

    def _get_hw_metrics_dict(self) -> Optional[dict]:
        summary_dict = {}
        if os.path.exists(self._ps_summary_csv_filename) and os.path.isfile(self._ps_summary_csv_filename):
            shutil.copy(self._ps_summary_csv_filename, self._ps_latest_csv_filename)
            with open(self._ps_latest_csv_filename, "r") as f:
                next(f)  # Skip the first line
                for line in f:
                    csv_data = line.split(",")
                    summary_dict[csv_data[0]] = csv_data[1]

            # For debug, print the json string here
            # summary_json_string = json.dumps(summary_dict)
            # print(summary_json_string, file=sys.stderr)
            os.remove(self._ps_latest_csv_filename)
            return summary_dict

        else:
            return None

    def _get_hw_metrics_html(self) -> Optional[str]:
        if os.path.exists(self._ps_summary_html_filename) and os.path.isfile(self._ps_summary_html_filename):
            encoded_html_data = None
            shutil.copy(self._ps_summary_html_filename, self._ps_latest_html_filename)
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

            os.remove(self._ps_latest_html_filename)
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
