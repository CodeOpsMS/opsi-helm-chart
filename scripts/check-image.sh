#!/usr/bin/env bash
# Exercise the finished image without starting OPSI or needing credentials/services.
set -euo pipefail
image=${1:?image reference required}
docker run --rm --entrypoint /usr/bin/python3 "$image" -c '
import ssl, urllib.request, tomllib, cryptography
from cryptography import x509
assert hasattr(x509, "load_pem_x509_certificates")
print("System Python TLS and certificate dependencies are available")
'
docker run --rm --entrypoint /bin/sh "$image" -ec '
check_package() {
  actual=$(dpkg-query -W -f="\${Version}" "$1")
  test "$actual" = "$2" || { echo "Unexpected package version: $1=$actual" >&2; exit 1; }
}
check_package opsi-directory-connector 53.2-1
check_package cifs-utils 2:7.4-1
check_package smbclient 2:4.22.11+dfsg-0+deb13u1
check_package opsiconfd 4.3.56.11-1
check_package opsi-utils 4.3.30.7-1
check_package python3-cryptography 43.0.0-3+deb13u1
test -x /entrypoint.sh
test -x /usr/bin/opsi-directory-connector
command -v smbclient
command -v mount.cifs
test "$(readlink /usr/bin/opsi-deploy-client-agent)" = /var/lib/opsi/depot/opsi-client-agent/opsi-deploy-client-agent
echo "Pinned OPSI and deployment packages verified"
'
