import os
import time
import errno

from typing import List, Tuple
from gprofiler.log import get_logger_adapter

logger = get_logger_adapter(__name__)

# See linux/printk.h
CONSOLE_EXT_LOG_MAX = 8192


class DevKmsgReader:
    # The /dev/kmsg interfaced is described at Documentation/ABI/testing/dev-kmsg in the kernel source tree
    # and can be viewed at https://github.com/torvalds/linux/blob/master/Documentation/ABI/testing/dev-kmsg.
    def __init__(self):
        self.dev_kmsg_fd = os.open("/dev/kmsg", os.O_RDONLY)
        os.set_blocking(self.dev_kmsg_fd, False)
        # skip all historical messages:
        os.lseek(self.dev_kmsg_fd, 0, os.SEEK_END)

    def iter_new_messages(self):
        messages: List[Tuple[float, bytes]] = []
        try:
            # Each read() is one message
            while True:
                message = os.read(self.dev_kmsg_fd, CONSOLE_EXT_LOG_MAX)
                messages.append((time.time(), message))
        except OSError as e:
            if e.errno != errno.EAGAIN:
                raise

        yield from self._parse_raw_messages(messages)

    @staticmethod
    def _parse_raw_messages(messages: List[Tuple[float, bytes]]):
        for timestamp, message in messages:
            prefix, text = message.decode().split(";", maxsplit=1)
            fields = prefix.split(",")
            level = int(fields[0])
            yield timestamp, level, text


class KernelMessagePublisher:
    def __init__(self, reader):
        self.reader = reader
        self.subscribers = []

    def handle_new_messages(self):
        for message in self.reader.iter_new_messages():
            for subscriber in self.subscribers:
                try:
                    subscriber(message[2])
                except Exception:
                    logger.exception(f"Error handling message: {message[2]}")

    def subscribe(self, callback):
        self.subscribers.append(callback)
