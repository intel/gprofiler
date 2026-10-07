#!/usr/bin/env bash

set -eu

# CentOS 7 reached EOL; mirror.centos.org no longer serves the repos.
# Repoint every repo at the archive.kernel.org snapshot of CentOS 7.9.2009.
#
# Three URL layouts must be handled (across x86_64 and aarch64 images):
#   x86_64 base:  mirror.centos.org/centos/$releasever/...  (also literal 7 in SCLo)
#   x86_64 SCLo:  mirror.centos.org/centos/7/sclo/...
#   aarch64:      mirror.centos.org/altarch/7/...
# which map to:
#   archive.kernel.org/centos-vault/7.9.2009/...          (x86_64)
#   archive.kernel.org/centos-vault/altarch/7.9.2009/...  (aarch64)

# Normalise the release token to the archived version (handles both $releasever and literal 7).
sed -i "s|/centos/\$releasever/|/centos/7.9.2009/|g" /etc/yum.repos.d/*.repo
sed -i 's|/centos/7/|/centos/7.9.2009/|g' /etc/yum.repos.d/*.repo
sed -i "s|/altarch/\$releasever/|/altarch/7.9.2009/|g" /etc/yum.repos.d/*.repo
sed -i 's|/altarch/7/|/altarch/7.9.2009/|g' /etc/yum.repos.d/*.repo

# Swap the dead hosts for the archive, preserving the path layout.
#   .../centos/7.9.2009/...  -> .../centos-vault/7.9.2009/...
#   .../altarch/7.9.2009/... -> .../centos-vault/altarch/7.9.2009/...
sed -i 's|mirror\.centos\.org/centos/|archive.kernel.org/centos-vault/|g' /etc/yum.repos.d/*.repo
sed -i 's|vault\.centos\.org/centos/|archive.kernel.org/centos-vault/|g' /etc/yum.repos.d/*.repo
sed -i 's|mirror\.centos\.org/altarch/|archive.kernel.org/centos-vault/altarch/|g' /etc/yum.repos.d/*.repo
sed -i 's|vault\.centos\.org/altarch/|archive.kernel.org/centos-vault/altarch/|g' /etc/yum.repos.d/*.repo

sed -i 's/^#.*baseurl=http/baseurl=http/g' /etc/yum.repos.d/*.repo
sed -i 's/^mirrorlist=http/#mirrorlist=http/g' /etc/yum.repos.d/*.repo
yum clean all
