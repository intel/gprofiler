#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b pyperf_caching https://github.com/IzabellaRaulin/bcc.git && cd bcc && git reset --hard 15f609e9b68baebdb513bfbfa7b08297798ee97a

# (after clone, because we copy the licenses)
# TODO support aarch64
if [ $(uname -m) != "x86_64" ]; then
    mkdir -p /bcc/root/share/bcc/examples/cpp/
    touch /bcc/root/share/bcc/examples/cpp/PyPerf
    exit 0
fi

mkdir build
cd build
cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root ..
make -C examples/cpp/pyperf -j -l VERBOSE=1 install
