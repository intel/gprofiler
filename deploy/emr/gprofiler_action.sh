#!/bin/bash

# Re-run as root:
test $EUID = 0 || exec sudo "$0" "$@"

version=latest
arch=$(uname -m)

wget "https://github.com/intel/gprofiler/releases/$version/download/gprofiler_$arch" -O gprofiler
chmod +x gprofiler
# SECURITY NOTE: Do not pass --token via command line arguments as it will be
# visible in /proc/<pid>/cmdline. Instead, use environment variables:
#   export GPROFILER_SERVER_TOKEN=<token>
#   export GPROFILER_SERVICE_NAME=<service>
# gprofiler uses configargparse with auto_env_var_prefix="gprofiler_" so these
# environment variables are automatically used for --token and --service-name.
setsid ./gprofiler -cu "$@" >/dev/null 2>&1 &
