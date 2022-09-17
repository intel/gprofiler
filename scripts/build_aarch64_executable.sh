#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# ubuntu 20.04
UBUNTU_VERSION=@sha256:82becede498899ec668628e7cb0ad87b6e1c371cb8a1e597d83a47fac21d6af3
# rust 1.62.0
RUST_VERSION=@sha256:bb238a1b44df339176dad3b02f4a6494589379e3eb21b84f3d424598d4aaaab0
# centos 7
CENTOS7_VERSION=@sha256:43964203bf5d7fe38c6fca6166ac89e4c095e2b0c0a28f6c7c678a1348ddc7fa
# centos 8
CENTOS8_VERSION=@sha256:a27fd8080b517143cbbbab9dfb7c8571c40d67d534bbdee55bd6c473f432b177
# golang 1.16.3
GOLANG_VERSION=@sha256:f7d3519759ba6988a2b73b5874b17c5958ac7d0aa48a8b1d84d66ef25fa345f1
# alpine 3.14.2
ALPINE_VERSION=@sha256:b06a5cf61b2956088722c4f1b9a6f71dfe95f0b1fe285d44195452b8a1627de7
# mcr.microsoft.com/dotnet/sdk:6.0-focal
DOTNET_BUILDER=@sha256:749439ff7a431ab4bc38d43cea453dff9ae1ed89a707c318b5082f9b2b25fa22

mkdir -p build/aarch64
docker buildx build --platform=linux/arm64 \
    --build-arg RUST_BUILDER_VERSION=$RUST_VERSION \
    --build-arg PYPERF_BUILDER_UBUNTU=$UBUNTU_VERSION \
    --build-arg PERF_BUILDER_UBUNTU=$UBUNTU_VERSION \
    --build-arg PHPSPY_BUILDER_UBUNTU=$UBUNTU_VERSION \
    --build-arg AP_BUILDER_CENTOS=$CENTOS7_VERSION \
    --build-arg AP_BUILDER_ALPINE=$ALPINE_VERSION \
    --build-arg AP_CENTOS_MIN=$CENTOS7_VERSION \
    --build-arg BURN_BUILDER_GOLANG=$GOLANG_VERSION \
    --build-arg GPROFILER_BUILDER=$CENTOS8_VERSION \
    --build-arg DOTNET_BUILDER=$DOTNET_BUILDER \
    . -f pyi.Dockerfile --output type=local,dest=build/aarch64/ $@
