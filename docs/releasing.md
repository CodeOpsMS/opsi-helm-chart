# CI and release promotion

## Candidate lifecycle

`Helm CI` runs strict lint, chart unit tests and Kubernetes 1.34 schema validation on Helm 3 and Helm 4. Release-gate tests reject pull-request/manual CI runs, missing runtime acceptance and mismatched source commits or archive checksums.

CI packages the chart exactly once. The artifact `opsi-chart-<run-id>-<attempt>` contains the `.tgz` and `candidate.json` with its source commit and SHA-256. Kind installs that archive with separate public MariaDB and Redis Stack fixtures; it never repackages the chart. CI credentials are generated for that ephemeral namespace and are not uploaded.

For a failed workflow, rerun all jobs so the candidate and runtime jobs share the same attempt. A release requires successful **main-push** CI; a PR or manually dispatched CI run can test a change but cannot be used as a release source.

## Real-cluster acceptance

Download the candidate artifact from the successful main CI run. Verify `candidate.json`, its source commit and the archive SHA-256 before installing the archive on the test cluster. Do not substitute a locally repackaged chart, even from the same source revision.

Record the acceptance described in [operations](operations.md), including actual PXE results. Keep cluster credentials and private configuration outside GitHub Actions. The release workflow requires an explicit non-secret acceptance reference and the exact SHA-256 of the locally accepted archive; this is an operator attestation backed by that recorded evidence, not an automated assertion that a physical boot succeeded.

## Repository setup

Before the first release:

- Enable immutable GitHub releases.
- Create the `gh-pages` branch and configure GitHub Pages to publish from its root.
- Allow the publishing job to use `contents: write`, `packages: write` and `pages: write`. The built-in `GITHUB_TOKEN` is sufficient; no personal access token or cluster credential is required.
- After the first OCI upload, set `helm-charts/opsi` to public and link it to this repository. If visibility is not yet public, the workflow stops before publishing the immutable release; rerun with the same candidate after correcting visibility.

No workflow changes these repository settings automatically. Protect `main` with the static and runtime checks appropriate to the repository's review policy.

## Publish

Dispatch `Release tested chart` from `main` with:

- `ci_run_id`: the successful main-push Helm CI run.
- `live_chart_sha256`: the SHA-256 from the real-cluster acceptance.
- `live_acceptance_reference`: the non-secret location/identifier of that acceptance record.

The workflow checks the CI source repository, event, branch, commit and each required job. It retrieves the candidate, verifies its provenance and the supplied live checksum, and checks out the tested source commit. A chart version is never reused for different bytes.

Publication uses the **same archive** for all destinations:

| Destination | Naming |
| --- | --- |
| GitHub release/tag | `opsi-<chart-version>` |
| Release asset and Pages archive | `opsi-<chart-version>.tgz` |
| OCI | `oci://ghcr.io/codeopsms/helm-charts/opsi:<chart-version>` |
| Helm repository | `https://codeopsms.github.io/opsi-helm-chart/` |

OCI and Pages must serve the expected checksum publicly before the GitHub draft is published as an immutable release. Prerelease chart versions are marked prerelease and cannot become GitHub Latest. The release step never runs `helm package`.

After updating `gh-pages`, the workflow explicitly requests a [GitHub Pages build](https://docs.github.com/en/rest/pages/pages#request-a-github-pages-build) before checking the public archive. A push made with `GITHUB_TOKEN` does not trigger a branch-based Pages build on its own. The build request also runs on release retries, so an interrupted first attempt can finish without another content change.

If publication stops partway through, rerun the same inputs. Existing matching archives are accepted; mismatching or unpullable existing OCI versions cause an error rather than an overwrite. Fix the publication issue or increment the chart version and repeat acceptance.
