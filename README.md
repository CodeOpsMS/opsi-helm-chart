# OPSI Helm chart

A community Helm chart for a persistent OPSI 4.3 config/depot server, including optional PXE/TFTP. The application uses external MySQL/MariaDB and Redis services. This repository is independent of the OPSI vendor.

The chart targets **OPSI 4.3.56.11**. Version **0.2.0** adds optional separate file storage for depot, repository and workbench, including explicitly configured NAS ownership mapping. Published versions and their immutable source commits are listed in [Releases](https://github.com/CodeOpsMS/opsi-helm-chart/releases). A chart release does not complete an existing server's production data migration.

The complete feature set uses the [application image built by this repository](docs/image.md), which extends the official OPSI image with the directory connector, SMB/CIFS client deployment tools and system-Python certificate support. Both the upstream base and deployed image are pinned by digest.

## Prerequisites

- Kubernetes 1.34 or later and Helm 3 or 4; the CI runtime baseline is Kubernetes 1.34.
- One writable persistent volume for OPSI configuration, identities, certificates, depot, repository and workbench data. The deployment runs at most one OPSI instance.
- An external MySQL-compatible database with credentials restricted to the OPSI database.
- An external Redis instance with RedisTimeSeries support. The CI fixtures use MariaDB 11.8.8 and Redis Stack containing Redis 7.4.7; ordinary Redis without the required module is not the tested configuration.
- Existing Kubernetes Secrets holding the administrator, database and Redis passwords.
- For PXE: a fixed LAN-reachable server address, appropriate DHCP boot settings and UDP/TFTP reachability. The chart does not install or reconfigure DHCP.

See the [OPSI container documentation](https://docs.opsi.org/opsi-docs-en/4.3/server/installation/docker.html) and [DHCP/PXE requirements](https://docs.opsi.org/opsi-docs-en/4.3/server/components/dhcp-server.html).

## Install

Create the referenced Secrets through your secret-management process before installing. Keep credentials out of values files and Git. A minimal values file contains:

```yaml
server:
  hostId: opsi.example.com
  externalUrl: https://opsi.example.com:4447
  configServiceUrls: [https://opsi.example.com:4447]
  ipAddress: '192.0.2.10'
  depotRemoteUrl: webdavs://opsi.example.com:4447/depot
  depotWebdavUrl: webdavs://opsi.example.com:4447/depot
  repositoryRemoteUrl: webdavs://opsi.example.com:4447/repository
  workbenchRemoteUrl: webdavs://opsi.example.com:4447/workbench
admin:
  username: adminuser
  existingSecret: opsi-admin
  passwordKey: password
mysql:
  host: database.example.com
  database: opsi
  username: opsi
  existingSecret: opsi-database
  passwordKey: password
redis:
  host: redis.example.com
  existingSecret: opsi-redis
  passwordKey: password
persistence:
  size: 100Gi
```

Replace the reserved example address and hostnames with your own endpoints. Allocate storage for your complete depot and installation media. Review [chart values](charts/opsi/values.yaml) and the chart schema for networking and storage options.

`server.externalUrl` selects the single HTTPS service endpoint used by the PXE boot image. Bootstrap adds an explicit port (443 when omitted) and `/rpc`. Windows clients retain all entries in `server.configServiceUrls` for failover.

Use a version listed in Releases:

```sh
helm upgrade --install opsi oci://ghcr.io/codeopsms/helm-charts/opsi \
  --version 0.2.0 --namespace opsi --create-namespace \
  --values opsi-values.yaml --wait --timeout 15m
helm test opsi --namespace opsi
```

The conventional repository is also available after publication:

```sh
helm repo add opsi https://codeopsms.github.io/opsi-helm-chart/
helm repo update
helm search repo opsi/opsi --devel
```

For development before publication, replace the OCI URL with `./charts/opsi`.

## Operations and GitOps

Pin the chart version and image digest. Store environment-specific values in the Fleet repository and reference Secrets that already exist in the destination namespace. Use the same release name and namespace during handover from a direct Helm installation to Fleet; verify resource ownership before enabling reconciliation.

The administrator API and WebDAV use native OPSI HTTPS on port 4447. Preserve the OPSI CA and server identity when migrating existing clients. A generic HTTP ingress is insufficient for PXE, and reverse-proxy configuration must retain the URLs and trust relationships clients use.

- [Operation, networking and acceptance](docs/operations.md)
- [Migration and rollback](docs/migration.md)
- [Separate file storage and NAS ownership mapping](docs/storage.md)
- [CI, live acceptance and release promotion](docs/releasing.md)

## Development checks

Install Helm, Python, zsh and ShellCheck before running the checks.

```sh
helm lint charts/opsi --strict -f tests/ci-values.yaml
python3 -m pip install cryptography==50.0.1
python3 -m unittest discover -s charts/opsi/tests/unit -v
python3 -m unittest discover -s tests -p 'test_*.py'
shellcheck scripts/*.sh
```

The CI values contain only reserved example addresses and Secret references. Disposable database/Redis fixtures belong to the CI harness and are not chart dependencies. `scripts/kind-acceptance.sh` refuses to run outside its designated GitHub Actions Kind job.
