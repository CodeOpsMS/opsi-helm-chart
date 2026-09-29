# The reference deployment adds the directory connector and client deployment tools.
# Keep the upstream entrypoint, supervisor and filesystem layout intact.
FROM uibmz/opsi-server:4.3@sha256:04ada8c27bbaa1049180d72b311a7daca7ee88a5221b1093285e4b374fcdb3ef

ARG SOURCE_SHA=development
LABEL org.opencontainers.image.source="https://github.com/CodeOpsMS/opsi-helm-chart" \
      org.opencontainers.image.revision="${SOURCE_SHA}" \
      org.opencontainers.image.version="4.3.56.11"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        opsi-directory-connector=53.2-1 \
        cifs-utils=2:7.4-1 \
        smbclient=2:4.22.11+dfsg-0+deb13u1 \
        opsiconfd=4.3.56.11-1 \
        opsi-utils=4.3.30.7-1 \
        python3-cryptography=43.0.0-3+deb13u1 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /var/lib/opsi/depot/opsi-client-agent/opsi-deploy-client-agent /usr/bin/opsi-deploy-client-agent
