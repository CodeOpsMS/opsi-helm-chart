# Valkey TimeSeries artifact for OPSI

This independent image supplies `libvalkey_timeseries.so` to the official SUSE Valkey container through an initContainer and shared volume. It does not replace the SUSE chart/server image and does not alter the published OPSI chart 0.1.0. The artifact targets **linux/amd64** and is published as `ghcr.io/codeopsms/valkey-timeseries-module:sha-<repository-commit>`; deployments must use the resulting manifest digest.

## Build inputs and compatibility patch

The [upstream project](https://github.com/opensource-for-valkey/valkey-timeseries) is pinned to commit [`82703430a89934b7f330aec1e332cabbec97ef49`](https://github.com/opensource-for-valkey/valkey-timeseries/tree/82703430a89934b7f330aec1e332cabbec97ef49). It has no release tag at the time of this integration. The builder uses Rust **1.96.0**, a digest-pinned Bookworm image, a dated Debian package snapshot and this repository's `Cargo.lock` with `cargo build --locked`. The lock was generated with Cargo 1.96.0 because upstream does not track one. Rust and C compiler flags explicitly select generic x86-64 rather than upstream's unconditional AVX2/BMI flags. Build inputs are fixed; deploy the accepted image digest rather than assuming rebuilds are byte-identical.

The explicit patch in `images/valkey-timeseries/patches/0001-opsi-module-name.patch` changes **only** registration name `ts` to `timeseries`. Unmodified opsiconfd 4.3.56.11 checks this name. The implementation remains Valkey TimeSeries; its module version, `TS.*` commands and native serialization type are unchanged. This is not RedisTimeSeries, and no RedisTimeSeries version is advertised. The upstream Apache-2.0 license and a notice of our change are included in the artifact image.

The registration change also changes the module configuration namespace. Configure strict compatibility as `timeseries.ts-compatibility-mode strict`; CI must confirm this exact key with `CONFIG GET timeseries.ts-compatibility-mode`. The module is built against glibc 2.36, below the target SUSE Valkey 9.1.2-7.1 / SLES 15 SP7 glibc 2.38. CI rejects unexpected ELF dependencies or a higher glibc requirement. It tests on digest-pinned public upstream Valkey **9.1.2** because SUSE registry credentials must not be copied into this public repository or GitHub Actions. Loading the accepted module into the exact SUSE image remains a required cluster acceptance step.

## Verification and publication

Pull requests build without publishing. From a Docker/Buildx host, run:

```sh
scripts/build-valkey-timeseries.sh valkey-timeseries-module:development "$(git rev-parse HEAD)"
```

The check executes the actual UID/GID 1000 init command, mounts only the resulting module into an isolated server and tests the wire contract used by OPSI: exact duplicate-create errors, rule parsing and replacement, repeated setup, duplicate SUM, empty LABELS, downsampling, range aggregation, same-module DUMP/RESTORE and semantic TS.MADD. Separate RDB and pure AOF tests persist samples, labels and rules, kill the server, restart with the same volume, and prove that aggregation resumes. Test ports bind only to localhost and all test containers/volumes are removed afterwards. Failure blocks publication.

After review and successful PR checks, run **Valkey TimeSeries module** on `main` with `publish=true`. It builds and verifies once, pushes that exact local image and attests its manifest digest. A source-SHA tag is never replaced with different content. A new GHCR package must be made public in its settings and checked with an anonymous pull before Fleet uses it. Neither this workflow nor its tests change package visibility or deploy into Kubernetes.

## InitContainer contract

The artifact contains `/bin/sh`, `cp`, `chmod` and `/module/libvalkey_timeseries.so`. Its default user is `1000:1000`; its default command copies the module with `cp -f` into `/out/libvalkey_timeseries.so` and sets mode `0555` (Valkey requires the execute bits when loading a module). Force replacement makes retries work when an earlier init already left a read-only file; CI repeats the init against the same volume. Mount an EmptyDir writable through `fsGroup: 1000` at `/out`. Mount that same volume read-only in the SUSE server at `/mnt/valkey/modules`, and configure:

```text
loadmodule /mnt/valkey/modules/libvalkey_timeseries.so
timeseries.ts-compatibility-mode strict
timeseries.ts-num-threads 2
```

The module's two background threads are explicit rather than derived from the host CPU count; CI verifies the exposed configuration key. The final container contains no Valkey server and does not copy shared system libraries into SUSE. Retain the official chart's authentication, persistence, health checks and server image. Confirm module identity, strict configuration, runtime dependencies and actual OPSI health/metrics before rollout.

## Data migration boundary

Upstream explicitly documents [incompatible native encodings](https://github.com/opensource-for-valkey/valkey-timeseries/blob/82703430a89934b7f330aec1e332cabbec97ef49/COMPATIBILITY.md). RedisTimeSeries RDB/AOF/DUMP blobs cannot be restored as Valkey TimeSeries. The same-module persistence tests here do not claim foreign-format compatibility. Migrate time-series metadata, samples and aggregation rules through semantic commands, or deliberately start a fresh metrics database under an approved migration plan. Keep the existing Redis data available for rollback until acceptance is complete.
