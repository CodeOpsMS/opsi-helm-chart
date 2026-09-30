# Build below the target SLES 15 SP7 glibc 2.38 baseline (Bookworm: 2.36).
FROM rust:1.96.0-bookworm@sha256:5e2214abe154fe26e39f64488952e5c991eeed1d6d6da7cc8381ae83927f0cfc AS builder

ENV UPSTREAM_SHA=82703430a89934b7f330aec1e332cabbec97ef49 \
    RUSTFLAGS="-C target-cpu=x86-64" \
    CFLAGS="-march=x86-64 -mtune=generic" \
    CXXFLAGS="-march=x86-64 -mtune=generic"

# Freeze compiler packages and their transitive dependencies as well as Rust.
RUN rm -f /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20260929T000000Z bookworm main' > /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends clang libclang-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /source
RUN git init . \
    && git remote add origin https://github.com/opensource-for-valkey/valkey-timeseries.git \
    && git fetch --depth=1 origin "$UPSTREAM_SHA" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$UPSTREAM_SHA"
COPY Cargo.lock /source/Cargo.lock
COPY patches/0001-opsi-module-name.patch /tmp/opsi-module-name.patch
# OPSI checks the registration name. Preserve upstream version, TS.* and data type.
RUN patch --fuzz=0 -p1 < /tmp/opsi-module-name.patch \
    && cargo build --locked --release --lib \
    && mkdir /module \
    && cp target/release/libvalkey_timeseries.so /module/ \
    && chmod 0444 /module/libvalkey_timeseries.so \
    && readelf -d /module/libvalkey_timeseries.so > /module/elf-dynamic.txt \
    && readelf --version-info /module/libvalkey_timeseries.so > /module/elf-versions.txt \
    && ldd /module/libvalkey_timeseries.so > /module/ldd.txt \
    && cat /module/ldd.txt \
    && ! grep -q 'not found' /module/ldd.txt

# This is an artifact courier, not another Valkey server image.
FROM busybox:1.37.0-glibc@sha256:42a9ff19af33811ee466b24c66384461d37c32dcc28d1d27d49b8aca816bd5ee
ARG SOURCE_SHA=development
LABEL org.opencontainers.image.source="https://github.com/CodeOpsMS/opsi-helm-chart" \
      org.opencontainers.image.revision="${SOURCE_SHA}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.description="Valkey TimeSeries artifact with documented OPSI registration-name patch" \
      io.codeopsms.valkey-timeseries.upstream="82703430a89934b7f330aec1e332cabbec97ef49" \
      io.codeopsms.valkey-timeseries.module-name="timeseries"
COPY --from=builder /module/libvalkey_timeseries.so /module/libvalkey_timeseries.so
COPY --from=builder /module/elf-dynamic.txt /module/elf-versions.txt /usr/share/valkey-timeseries/
COPY --from=builder /source/LICENSE /usr/share/licenses/valkey-timeseries/LICENSE
COPY NOTICE /usr/share/valkey-timeseries/NOTICE
USER 1000:1000
ENTRYPOINT ["/bin/sh", "-ec", "cp -f /module/libvalkey_timeseries.so /out/libvalkey_timeseries.so && chmod 0444 /out/libvalkey_timeseries.so"]
