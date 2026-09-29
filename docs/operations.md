# Operation and acceptance

## Identity and storage

Set `server.hostId` to the stable, fully qualified OPSI server identity. Configure externally reachable service and depot URLs explicitly; Kubernetes Service DNS is generally unsuitable for managed laptops.

The application PVC retains the mutable `/etc/opsi`, `/var/lib/opsi` and log state managed by the chart. Review the mounted paths in the rendered deployment and back up all authoritative data, including external database state. PXE has a separate persistent TFTP volume. `existingClaim` allows storage to be provisioned independently of Helm; retention protects data at uninstall but is not a backup.

Keep one active writer. Scale to zero before an offline restore. An application restart or upgrade must retain the host key, CA, product data, server identity and credentials. Do not attempt application rollback against a database schema that a newer version has already migrated without the supported restore procedure.

## Secret lifecycle

Supply passwords through existing Secrets. The chart does not generate replacement passwords during Fleet rendering. Provision the database user and its database outside this chart. Coordinate any credential rotation with the external service and an explicit OPSI pod restart; a Helm values change alone cannot detect the contents of an externally managed Secret.

Additional CA certificates and trusted proxy configuration must match the actual network path. Preserve native HTTPS verification. The test harness reads only the public OPSI CA certificate and verifies the server hostname through its local port-forward.

Additional CA inputs are deduplicated and appended while preserving the native OPSI CA and existing trust. Removing a CA from values does not revoke it from the persisted bundle; plan explicit trust removal when required. Restart the pod after changing an externally managed CA ConfigMap.

Keep the optional directory connector disabled or suspended for an isolated test. When enabling it, limit synchronization to the intended directory scope and keep its configuration in the referenced Secret.

## PXE and DHCP

Enable PXE only with the chart's supported direct-network mode and a suitable node selector. Ensure the selected node can bind UDP 69 and the TFTP transfer ports. A fixed host-network address couples PXE availability to that selected node; it is not a highly available virtual address.

An existing DHCP server supplies the bootserver and architecture-specific filename. For x86_64 UEFI the OPSI filename is `opsi/loader/opsi-netboot.x64.efi`. Legacy BIOS uses `opsi/loader/opsi-netboot.x86.bios`. See [OPSI DHCP/PXE](https://docs.opsi.org/opsi-docs-en/4.3/server/components/dhcp-server.html).

For initial acceptance, scope the DHCP policy to the test client's MAC. Use an isolated test VM or a designated spare device. Do not change network-wide boot settings for an application smoke test. A successful small TFTP transfer does not establish that the full boot image or the installation workflow works.

## Acceptance evidence

Record the chart archive SHA-256, source commit, image digest, Kubernetes version and the actual test outcome. Do not include passwords, host keys, client inventories or private certificate keys in public reports.

The GitHub Kind job checks:

1. External MariaDB and authenticated Redis Stack, including the TimeSeries module.
2. Native OPSI startup and the authenticated Helm test.
3. Rejection of unauthenticated host API requests, successful authenticated API access and expected server identity.
4. Authenticated depot WebDAV and write/read through the workbench.
5. Unchanged server host key, workbench content, trusted native HTTPS and functioning authentication after a pod restart and Helm upgrade of the same candidate. The host key is compared by a private temporary fingerprint and never printed.

Real-cluster acceptance additionally verifies the external DNS/TLS path, actual storage, Fleet rendering and reconciliation, complete TFTP transfers, and an end-to-end UEFI boot with `hwinvent` on a designated client. Hardware inventory should be visible under that client, with no installation or wipe scheduled. Test operating-system installation separately when the required netboot product and licensed installation files are available.

After collecting results, stop or remove temporary clients and remove only their temporary DHCP boot overrides. Keep the OPSI test instance and its data according to the agreed environment lifecycle.
