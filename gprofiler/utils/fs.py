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
from pathlib import Path
from secrets import token_hex
from typing import Union

from granulate_utils.linux.ns import is_root

from gprofiler.platform import is_windows
from gprofiler.utils import remove_path, run_process


def safe_copy(src: str, dst: str) -> None:
    """
    Safely copies 'src' to 'dst'. Safely means that writing 'dst' is performed at a temporary location,
    and the file is then moved, making the filesystem-level change atomic.
    """
    dst_tmp = f"{dst}.tmp"
    shutil.copy(src, dst_tmp)
    os.rename(dst_tmp, dst)


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
