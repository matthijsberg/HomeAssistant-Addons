ARG BUILD_FROM=alpine:3.20
FROM matrixconduit/matrix-conduit:v0.8.0 AS conduit-source

FROM ${BUILD_FROM}

ENV LANG=C.UTF-8

# Install runtime dependencies
RUN apk add --no-cache \
    jq \
    curl \
    ca-certificates \
    libgcc \
    libstdc++ \
    sqlite-dev

# Copy conduit binary from official image
COPY --from=conduit-source /srv/conduit/conduit /usr/local/bin/conduit
RUN chmod +x /usr/local/bin/conduit

# Copy entrypoint script
COPY run.sh /run.sh
RUN chmod +x /run.sh

EXPOSE 6167

CMD [ "/run.sh" ]
