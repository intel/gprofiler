#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y curl git build-essential iperf llvm-9-dev libclang-9-dev \
  cmake python3 flex bison libelf-dev libz-dev liblzma-dev

cd /tmp

# Install libunwind
curl -L http://download.savannah.nongnu.org/releases/libunwind/libunwind-1.4.0.tar.gz -o libunwind-1.4.0.tar.gz
tar -xf libunwind-1.4.0.tar.gz
pushd libunwind-1.4.0
./configure --prefix=/usr && make install
popd
rm -r libunwind-1.4.0
rm libunwind-1.4.0.tar.gz
