# OPSI application image

The full reference feature set requires the image built by this repository. The upstream OPSI image alone does not contain all directory-connector and client-deployment tools used here. `Dockerfile` preserves the official entrypoint and adds exact package versions for the connector, SMB/CIFS client tools, the targeted OPSI server/utilities and system-Python certificate support. The deploy-client-agent link resolves after the corresponding OPSI product has been installed in the depot.

The upstream base is fixed by its multi-platform registry digest. The resulting image currently targets **linux/amd64**. Direct package versions are pinned and builds fail if they are unavailable; upstream APT indexes and transitive dependencies are not a historical snapshot. Rebuilding a commit is therefore not guaranteed to produce identical bytes. Deploy the published manifest digest, never a floating tag.

## Initial bootstrap

1. Merge `Dockerfile`, `.dockerignore`, the three `scripts/*-image.sh` files, this document and `.github/workflows/image.yml` to `main`. The image workflow is independent of the chart and external database/Redis services.
2. Run **OPSI image** manually on `main`. Pull requests build and validate only; manual runs on other branches cannot publish.
3. The job builds once, verifies package versions and the system Python TLS/certificate imports, pushes that image as `ghcr.io/codeopsms/opsi-server:sha-<full-source-commit>` and creates a provenance attestation for the resulting manifest digest. The digest appears in the job summary.
4. For a new GHCR package, make `opsi-server` public in its package settings and verify anonymous pulls before using it in public chart CI. This workflow does not change package visibility.
5. Pin that image digest in the chart, then run the chart CI and cluster acceptance against the packaged chart. Image publication is not chart release or PXE acceptance.

Source-SHA tags are never overwritten with different image content. If a build must change, use a new source commit. Updating package pins or the upstream base requires a reviewed change and new image validation. The package API check distinguishes an absent package from other API failures; concurrent manual image publications are serialized.

## Local verification

With Docker and Buildx installed, from the repository root:

```sh
scripts/build-image.sh opsi-server:development "$(git rev-parse HEAD)"
```

This does not start the server or require secrets, Kubernetes or a running database. Runtime API, WebDAV, persistence and PXE checks belong to chart acceptance.
