#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -e

mkdir -p build

function curl_with_timecond() {
    url="$1"
    output="$2"
    if [ -f "$output" ]; then
        time_cond="-z $output"
    else
        time_cond=""
    fi
    curl -fL "$url" $time_cond -o "$output"
}

# async-profiler
mkdir -p gprofiler/resources/java

curl_with_timecond https://github.com/Granulate/async-profiler/releases/download/v2.0g1/async-profiler-2.0-linux-x64.tar.gz build/async-profiler-2.0-linux-x64.tar.gz
tar -xzf build/async-profiler-2.0-linux-x64.tar.gz -C gprofiler/resources/java --strip-components=2 async-profiler-2.0-linux-x64/build

# pyperf - just create the directory for it, it will be built later
mkdir -p gprofiler/resources/python/pyperf

# perf
curl_with_timecond https://github.com/Granulate/linux/releases/download/v5.12g1/perf gprofiler/resources/perf
chmod +x gprofiler/resources/perf

# burn
curl_with_timecond https://github.com/Granulate/burn/releases/download/v1.0.1g2/burn gprofiler/resources/burn
chmod +x gprofiler/resources/burn

rm -r build
