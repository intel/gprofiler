#!/usr/bin/env bash

set -euo pipefail

retry() {
  local retries="$1"
  local command="$2"

  echo "→ Running: $command (retries left: $retries)"
  eval "$command"
  local exit_code=$?

  if [[ $exit_code -ne 0 && $retries -gt 0 ]]; then
    echo "⚠️  $command failed with exit code $exit_code, retrying..."
    retry $((retries - 1)) "$command"
    exit_code=$?
  elif [[ $exit_code -ne 0 ]]; then
    echo "❌ $command failed after retries."
  fi

  return $exit_code
}
# update libmodulemd to fix https://bugzilla.redhat.com/show_bug.cgi?id=2004853
retry 3 "yum install -y epel-release libmodulemd" && yum clean all


retry 3 "yum install -y bzip2-devel libffi-devel perl-core zlib-devel xz-devel ca-certificates wget" && yum clean all
retry 3 "yum groupinstall -y "Development Tools"" && yum clean all
