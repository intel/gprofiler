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

import errno
import os
import shutil
import stat
from pathlib import Path
from secrets import token_hex
from typing import Union

from granulate_utils.linux.ns import is_root

from gprofiler.platform import is_windows
from gprofiler.utils import remove_path, run_process


def _is_symlink_lstat(path: str) -> bool:
    """Check if path is a symlink without following it."""
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


def safe_copy(src: str, dst: str) -> None:
    """
    Safely copies 'src' to 'dst'. Safely means that writing 'dst' is performed at a temporary location,
    and the file is then moved, making the filesystem-level change atomic.

    Security: Uses O_EXCL to atomically create the temp file, preventing symlink attacks where an
    attacker plants a symlink to redirect writes to arbitrary locations.
    """
    dst_tmp = f"{dst}.tmp"

    # Remove existing tmp file if it's a regular file (from interrupted previous copy)
    # If it's a symlink, refuse to proceed
    if os.path.lexists(dst_tmp):
        if _is_symlink_lstat(dst_tmp):
            raise Exception(f"Refusing to copy to {dst_tmp}: path is a symlink")
        os.unlink(dst_tmp)

    # O_EXCL ensures atomic creation - fails if anything exists at path (including symlinks).
    # This closes TOCTOU race between the check above and the open.
    # EEXIST means another process created the file after our delete - indicates a race or attack.
    try:
        fd = os.open(dst_tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise Exception(
            f"Refusing to copy: {dst_tmp} was created unexpectedly (possible race condition or symlink attack)"
        )
    try:
        dst_file = os.fdopen(fd, "wb")
    except Exception:
        os.close(fd)
        try:
            os.unlink(dst_tmp)
        except OSError:
            pass
        raise
    try:
        with dst_file, open(src, "rb") as src_file:
            shutil.copyfileobj(src_file, dst_file)
        # Preserve source file permissions (e.g., executable bit)
        shutil.copymode(src, dst_tmp)
    except Exception:
        try:
            os.unlink(dst_tmp)
        except OSError:
            pass
        raise

    # Check dst is not a symlink before final rename
    if _is_symlink_lstat(dst):
        os.unlink(dst_tmp)
        raise Exception(f"Refusing to rename to {dst}: path is a symlink")

    os.rename(dst_tmp, dst)


def safe_read_text(path: str) -> str:
    """
    Safely read text from a file, refusing to follow symlinks.

    Uses O_NOFOLLOW so the kernel rejects symlinks atomically at open time (Linux).
    On platforms without O_NOFOLLOW the flag falls back to 0 and the protection
    is best-effort; the target platform for this code is Linux where O_NOFOLLOW
    is always available.

    Raises if path is a symlink.
    """
    try:
        # O_NOFOLLOW makes open() fail with ELOOP if the path is a symlink (Linux-specific behavior).
        # On platforms without O_NOFOLLOW the flag is 0 and the call may follow symlinks; the target
        # platform for this code is Linux, so O_NOFOLLOW is always available.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise Exception(f"Refusing to read {path}: path is a symlink")
        raise

    try:
        f = os.fdopen(fd, "r")
    except Exception:
        os.close(fd)
        raise
    with f:
        return f.read()


def is_rw_exec_dir(path: Path) -> bool:
    """
    Is 'path' rw and exec?
    """
    assert is_owned_by_root(path), f"expected {path} to be owned by root!"

    # randomize the name - this function runs concurrently on paths of in same mnt namespace.
    test_script = path / f"t-{token_hex(10)}.sh"

    # try creating & writing
    try:
        test_script.write_text("#!/bin/sh\nexit 0")
        test_script.chmod(0o755)  # make sure it's executable. file is already writable only by root due to umask.
    except OSError as e:
        if e.errno == errno.EROFS:
            # ro
            return False
        remove_path(test_script)
        raise

    # try executing
    try:
        run_process([str(test_script)], suppress_log=True, pdeathsigger=False)
    except PermissionError:
        # noexec
        return False
    finally:
        test_script.unlink()

    return True


def escape_filename(filename: str) -> str:
    return filename.replace(":", "-" if is_windows() else ":")


def is_owned_by_root(path: Path) -> bool:
    statbuf = path.stat()
    return statbuf.st_uid == 0 and statbuf.st_gid == 0


def is_owned_by_current_user(path: Path) -> bool:
    """Check if path is owned by the current user."""
    statbuf = path.stat()
    return statbuf.st_uid == os.getuid()


def mkdir_owned_root_wrapper(path: Union[str, Path], mode: int = 0o755) -> None:
    """
    Ensures a directory exists and is owned by the current user.

    If the directory exists and is owned by the current user, it is left as is.
    If the directory exists and is not owned by the current user, the function raises.
    If the directory doesn't exist, it is created.
    """
    if is_root():
        return mkdir_owned_root(path)

    path = path if isinstance(path, Path) else Path(path)
    if path.exists() or path.is_symlink():
        # Check for symlink first (don't follow it)
        if path.is_symlink():
            raise Exception(f"{str(path)} is a symlink, refusing to use it")
        if is_owned_by_current_user(path):
            return
        # Directory exists but is not owned by current user - can't use it safely
        raise Exception(f"{str(path)} exists but is not owned by current user")

    try:
        os.mkdir(path, mode=mode)
    except FileExistsError:
        # likely racing with another thread of gprofiler. as long as the directory is owned by current user, we're good.
        pass

    # Verify ownership and not a symlink after creation
    if path.is_symlink() or not is_owned_by_current_user(path):
        raise Exception(f"Failed to create directory {str(path)} as owned by current user")


def mkdir_owned_root(path: Union[str, Path], mode: int = 0o755) -> None:
    """
    Ensures a directory exists and is owned by root.

    If the directory exists and is owned by root, it is left as is.
    If the directory exists and is not owned by root, it is removed and recreated. If after recreation
    it is still not owned by root, the function raises.
    """

    path = path if isinstance(path, Path) else Path(path)
    # parent is expected to be root - otherwise, after we create the root-owned directory, it can be removed
    # as re-created as non-root by a regular user.
    if is_root() and not is_owned_by_root(path.parent):
        raise Exception(f"expected {path.parent} to be owned by root!")

    if path.exists() or path.is_symlink():
        # Check for symlink first (don't follow it)
        if path.is_symlink():
            raise Exception(f"{str(path)} is a symlink, refusing to use it")
        if is_root() and is_owned_by_root(path):
            return

        shutil.rmtree(path)

    try:
        os.mkdir(path, mode=mode)
    except FileExistsError:
        # likely racing with another thread of gprofiler. as long as the directory is root after all, we're good.
        pass

    # Verify ownership and not a symlink after creation
    if path.is_symlink() or (is_root() and not is_owned_by_root(path)):
        # lost race with someone else?
        raise Exception(f"Failed to create directory {str(path)} as owned by root")
