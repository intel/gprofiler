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
Setup for the *fast* agent acceptance tests.

These tests are intentionally kept out of the ``tests/`` package: that package's
conftest imports docker and the full profiler stack for end-to-end runs, which is
slow and needs a container runtime. The tests here are pure/in-process and only
depend (at most) on ``granulate_utils`` and ``psutil``, so they run in
milliseconds in CI.

This conftest makes the repo root and the vendored ``granulate-utils`` source
importable, so ``gprofiler.*`` and ``granulate_utils.*`` resolve without an
editable install.
"""

import sys
from pathlib import Path

# tests_fast/ -> parents[1] == repo root (the directory containing gprofiler/)
_REPO_ROOT = Path(__file__).resolve().parents[1]

_CANDIDATE_PATHS = (
    _REPO_ROOT,
    _REPO_ROOT / "granulate-utils",  # vendored granulate_utils source
)

for _path in _CANDIDATE_PATHS:
    _str = str(_path)
    if _path.is_dir() and _str not in sys.path:
        sys.path.insert(0, _str)
