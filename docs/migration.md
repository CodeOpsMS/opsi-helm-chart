# Migration from an existing OPSI container deployment

## Rehearsal

Inventory the running image digest, OPSI packages, plugins, actual Docker bind mounts/volumes, SQL and Redis versions, TLS certificates, server identity, host keys, depot URLs, directory synchronization and external DHCP configuration. Treat the running system as authoritative when a Compose example differs from its mounts.

Build an isolated test instance with its own database, Secrets, persistent volumes and test name. Keep directory synchronization suspended and use explicit test clients. A copied database may contain production client identities and authentication material; ensure that the rehearsal cannot accept or schedule production-client actions.

Take a consistent database backup and filesystem copy. Quiesce writers while capturing the final consistent pair. Include configuration, certificates/keys, depot, repository, workbench and boot files. The OPSI backup utility alone is not a substitute for separately preserving every depot and boot-data directory. Validate the archive and practice restoration before cutover.

Use the OPSI-supported restore/identity procedure for the exact installed version. Avoid ad hoc database edits. Preserve ownership and permissions, inspect restored mounts, and start the test only after its own endpoints and client scope are correct.

## Cutover

1. Record the tested chart version/archive digest and the exact external dependency versions. Keep the old deployment and a consistent backup available.
2. Schedule the interruption, stop new actions and synchronization, and quiesce the existing server.
3. Capture and verify the final database/filesystem backup; restore the final pair into the destination while OPSI is stopped.
4. Preserve the production server identity, host keys and certificate trust chain. Verify externally advertised URLs before enabling clients.
5. Start the single Kubernetes instance and verify authentication, server identity, products, depot/workbench access and existing clients.
6. Change only the approved DNS/proxy/DHCP mappings, test from a client network, and let the pinned Fleet bundle reconcile the accepted release.

Do not run two writable servers against one database or present two independent servers as the same production identity. An initially empty test installation and a production-data migration are separate acceptance stages.

## Rollback

Keep the previous DNS/proxy/DHCP values and consistent pre-cutover database/filesystem backup. If the new instance fails acceptance, stop its writers first, restore the consistent previous state if necessary, and restore the old network mappings before restarting the old server. Verify a real client action after rollback. Retain the failed instance's non-sensitive diagnostics separately for investigation.

PVC retention and `helm rollback` do not reverse application database migrations or synchronize data written after cutover. Document the accepted recovery point and any actions performed during the cutover window.
