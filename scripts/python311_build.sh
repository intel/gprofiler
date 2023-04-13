#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

wget https://www.python.org/ftp/python/3.11.3/Python-3.11.3.tgz
tar -xzf Python-3.11.3.tgz
cd Python-3.11.3
./configure --enable-shared --prefix=/usr LDFLAGS="-Wl,-rpath /usr/lib"
make -j "$(nproc)" && make -j "$(nproc)" altinstall
