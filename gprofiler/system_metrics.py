import statistics
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from threading import Event, RLock, Thread
from typing import List, Optional

import subprocess
import psutil

from gprofiler.exceptions import ThreadStopTimeoutError

DEFAULT_POLLING_INTERVAL_SECONDS = 5
STOP_TIMEOUT_SECONDS = 2


@dataclass
class Metrics:
    # The average CPU usage between gProfiler cycles
    cpu_avg: Optional[float]
    # The average RAM usage between gProfiler cycles
    mem_avg: Optional[float]
    # The CPU frequency between gProfiler cycles
    cpu_freq: Optional[float]
    # The CPI between gProfiler cycles
    cpu_cpi: Optional[float]
    # The CPU TMA frontend bound between gProfiler cycles
    cpu_tma_fe_bound: Optional[float]
    # The CPU TMA backend bound between gProfiler cycles
    cpu_tma_be_bound: Optional[float]
    # The CPU TMA bad speculation between gProfiler cycles
    cpu_tma_bad_spec: Optional[float]
    # The CPU TMA retiring between gProfiler cycles
    cpu_tma_retiring: Optional[float]

class SystemMetricsMonitorBase(metaclass=ABCMeta):
    @abstractmethod
    def start(self) -> None:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    @abstractmethod
    def _get_average_memory_utilization(self) -> Optional[float]:
        raise NotImplementedError

    @abstractmethod
    def _get_cpu_utilization(self) -> Optional[float]:
        """
        Returns the CPU utilization percentage since the last time this method was called.
        """
        raise NotImplementedError

    @abstractmethod
    def _get_hw_metrics(self) -> Optional[List[float]]:
        """
        Returns the CPU frequency since the last time this method was called.
        """
        raise NotImplementedError

    def get_metrics(self) -> Metrics:
        hw_metrics = self._get_hw_metrics()
        return Metrics(self._get_cpu_utilization(), self._get_average_memory_utilization(), hw_metrics[0], hw_metrics[1], hw_metrics[2], hw_metrics[3], hw_metrics[4], hw_metrics[5])


class SystemMetricsMonitor(SystemMetricsMonitorBase):
    def __init__(self, stop_event: Event, polling_rate_seconds: int = DEFAULT_POLLING_INTERVAL_SECONDS):
        self._polling_rate_seconds = polling_rate_seconds
        self._mem_percentages: List[float] = []
        self._stop_event = stop_event
        self._thread: Optional[Thread] = None
        self._lock = RLock()
        self._perfspect_thread: Optional[Thread] = None
        self._hw_metrics = {'cpu_freq':[], 'cpu_cpi':[], 'cpu_tma_fe_bound':[], 'cpu_tma_be_bound':[], 'cpu_tma_bad_spec':[], 'cpu_tma_retiring':[]}
        self._ps_process = None

        self._get_cpu_utilization()  # Call this once to set the necessary data

    def start(self) -> None:
        assert self._thread is None, "SystemMetricsMonitor is already running"
        assert self._perfspect_thread is None, "Perfspect is already running"
        assert not self._stop_event.is_set(), "Stop condition is already set (perhaps gProfiler was already stopped?)"
        self._thread = Thread(target=self._continuously_poll_memory, args=(self._polling_rate_seconds,))
        self._thread.start()

        ps_cmd = ['/tmp/perfspect', 'metrics', '--metrics', '"CPU operating frequency (in GHz)","CPI","TMA_Frontend_Bound(%)","TMA_Bad_Speculation(%)","TMA_Backend_Bound(%)","TMA_Retiring(%)"', '--duration', '0', '--live', '--format', 'csv', '--interval', '10']
        self._ps_process = subprocess.Popen(ps_cmd, stdout=subprocess.PIPE)
#        ps_stdout, ps_stderr = ps_process.communicate()
#        try:
#            # wait 2 seconds to ensure it starts
#            ps_process.wait(2)
#        except subprocess.TimeoutExpired:
#            pass
#        else:
#            raise Exception(f"Command {ps_cmd} exited unexpectedly with {ps_process.returncode}")
        self._perfspect_thread = Thread(target=self._continuously_poll_perfspect, args=(self._polling_rate_seconds,))
        self._perfspect_thread.start()

    def stop(self) -> None:
        assert self._thread is not None, "SystemMetricsMonitor is not running"
        assert self._perfspect_thread is not None, "Perfspect is not running"
        assert self._stop_event.is_set(), "Stop event was not set before stopping the SystemMetricsMonitor"
        self._thread.join(STOP_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            raise ThreadStopTimeoutError("Timed out while waiting for the SystemMetricsMonitor internal thread to stop")
        self._thread = None
        self._ps_process.kill()
        self._perfspect_thread.join(STOP_TIMEOUT_SECONDS)
        if self._perfspect_thread.is_alive():
            raise ThreadStopTimeoutError("Timed out while waiting for the SystemMetricsMonitor Perfspect thread to stop")
        self._perfspect_thread = None

    def _continuously_poll_memory(self, polling_rate_seconds: int) -> None:
        while not self._stop_event.is_set():
            current_ram_percent = psutil.virtual_memory().percent  # type: ignore # virtual_memory doesn't have a
            # return type is types-psutil
            self._mem_percentages.append(current_ram_percent)
            self._stop_event.wait(timeout=polling_rate_seconds)

    def _continuously_poll_perfspect(self, polling_rate_seconds: int) -> None:
        while not self._stop_event.is_set():
            metrics_str = self._ps_process.stdout.readline().decode()
            print(metrics_str)
            if metrics_str.startswith('TS,SKT,CPU,CID'):
                continue
            metric_values = metrics_str.split(',')
            if len(metric_values) > 0:
                self._hw_metrics['cpu_freq'].append(float(metric_values[4]))
                self._hw_metrics['cpu_cpi'].append(float(metric_values[5]))
                self._hw_metrics['cpu_tma_fe_bound'].append(float(metric_values[6]))
                self._hw_metrics['cpu_tma_bad_spec'].append(float(metric_values[7]))
                self._hw_metrics['cpu_tma_be_bound'].append(float(metric_values[8]))
                self._hw_metrics['cpu_tma_retiring'].append(float(metric_values[9]))
            self._stop_event.wait(timeout=polling_rate_seconds)

    def _get_average_memory_utilization(self) -> Optional[float]:
        # Make sure there's only one thread that takes out the values
        # NOTE - Since there's currently only a single consumer, this is not necessary but is done to support multiple
        # consumers.
        with self._lock:
            current_length = len(self._mem_percentages)
            if current_length == 0:
                return None
            average_memory = statistics.mean(self._mem_percentages[:current_length])
            self._mem_percentages[:current_length] = []
            return average_memory

    def _get_cpu_utilization(self) -> float:
        # None-blocking call. Must be called at least once before attempting to get a meaningful value.
        # See `psutil.cpu_percent` documentation.
        return psutil.cpu_percent(interval=None)

    def _get_hw_metrics(self) -> List[float]:
        current_length = len(self._hw_metrics['cpu_freq'])
        if current_length == 0:
            return None

        metric_list = []
        metric_list.append(statistics.mean(self._hw_metrics['cpu_freq'][:current_length]))
        metric_list.append(statistics.mean(self._hw_metrics['cpu_cpi'][:current_length]))
        metric_list.append(statistics.mean(self._hw_metrics['cpu_tma_fe_bound'][:current_length]))
        metric_list.append(statistics.mean(self._hw_metrics['cpu_tma_be_bound'][:current_length]))
        metric_list.append(statistics.mean(self._hw_metrics['cpu_tma_bad_spec'][:current_length]))
        metric_list.append(statistics.mean(self._hw_metrics['cpu_tma_retiring'][:current_length]))

        self._hw_metrics['cpu_freq'] = []
        self._hw_metrics['cpu_cpi'] = []
        self._hw_metrics['cpu_tma_fe_bound'] = []
        self._hw_metrics['cpu_tma_be_bound'] = []
        self._hw_metrics['cpu_tma_bad_spec'] = []
        self._hw_metrics['cpu_tma_retiring'] = []

        return metric_list
    

class NoopSystemMetricsMonitor(SystemMetricsMonitorBase):
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def _get_average_memory_utilization(self) -> Optional[float]:
        return None

    def _get_cpu_utilization(self) -> Optional[float]:
        return None

    def _get_hw_metrics(self) -> Optional[List[float]]:
        return None
