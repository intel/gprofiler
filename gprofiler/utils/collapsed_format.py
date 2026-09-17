from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from gprofiler.gprofiler_types import ProcessToStackSampleCounters, StackToSampleCount
from gprofiler.log import get_logger_adapter

logger = get_logger_adapter(__name__)


def parse_one_collapsed(collapsed: str, add_comm: Optional[str] = None) -> StackToSampleCount:
    """
    Parse a stack-collapsed listing.

    If 'add_comm' is not None, add it as the first frame for each stack.
    """
    stacks: StackToSampleCount = Counter()
    bad_lines = []
    total_lines = 0
    parsed_lines = 0

    for line in collapsed.splitlines():
        total_lines += 1
        line = line.strip()
        
        if line == "":
            continue
        if line.startswith("#"):
            continue
            
        try:
            stack, _, count_str = line.rpartition(" ")
            
            # Validate that we have both stack and count
            if not stack or not count_str:
                bad_lines.append(f"Missing stack or count: '{line}'")
                continue
                
            # Validate that count is actually a number
            count = int(count_str)
            if count < 0:
                bad_lines.append(f"Negative count: '{line}'")
                continue
                
            if add_comm is not None:
                stacks[f"{add_comm};{stack}"] += count
            else:
                stacks[stack] += count
            parsed_lines += 1
            
        except ValueError as e:
            bad_lines.append(f"Invalid count format: '{line}' - {str(e)}")
        except Exception as e:
            bad_lines.append(f"Parse error: '{line}' - {str(e)}")

    # Log statistics and bad lines for debugging
    if bad_lines:
        bad_count = len(bad_lines)
        logger.warning(
            f"Collapsed format parsing issues: {bad_count}/{total_lines} lines failed, "
            f"{parsed_lines} lines successfully parsed. First 5 bad lines: "
            + "; ".join(bad_lines[:5])
        )
        
        # If more than 50% of lines are bad, this might be corrupted py-spy output
        if bad_count > total_lines * 0.5:
            logger.error(
                f"Collapsed format severely corrupted: {bad_count}/{total_lines} bad lines. "
                f"This may indicate py-spy crash or output corruption."
            )
    else:
        logger.debug(f"Collapsed format parsed successfully: {parsed_lines}/{total_lines} lines")

    return stacks


def parse_one_collapsed_file(collapsed: Path, add_comm: Optional[str] = None) -> StackToSampleCount:
    """
    Parse a stack-collapsed file.
    """
    return parse_one_collapsed(collapsed.read_text(), add_comm)


def parse_many_collapsed(text: str) -> ProcessToStackSampleCounters:
    """
    Parse a stack-collapsed listing where stacks are prefixed with the command and pid/tid of their
    origin.
    """
    results: ProcessToStackSampleCounters = defaultdict(Counter)
    bad_lines = []

    for line in text.splitlines():
        try:
            stack, count = line.rsplit(" ", maxsplit=1)
            comm_pid_tid, stack = stack.split(";", maxsplit=1)
            comm, pid_tid = comm_pid_tid.rsplit("-", maxsplit=1)
            pid = int(pid_tid.split("/")[0])
            results[pid][f"{comm};{stack}"] += int(count)
        except ValueError:
            bad_lines.append(line)

    if bad_lines:
        logger.warning(f"Got {len(bad_lines)} bad lines when parsing (showing up to 8):\n" + "\n".join(bad_lines[:8]))

    return results
