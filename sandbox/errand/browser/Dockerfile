# The errand Agent's browser: OpenClaw's sandbox browser image, with Chromium wrapped so that it always goes through
# Sentinel (--proxy-server, loopback included) and accepts only certificates chaining to Sentinel's CA key.
FROM openclaw-sandbox-browser:bookworm-slim
ARG CA_SPKI
USER root
COPY ca.pem /etc/mousse/sentinel-ca.pem
COPY browser/chromium-sentinel.sh /usr/local/bin/chromium
RUN test -n "$CA_SPKI" && echo "$CA_SPKI" > /etc/mousse/sentinel-ca.spki \
  && cp /etc/mousse/sentinel-ca.pem /etc/ssl/certs/ca-certificates.crt \
  && chmod 755 /usr/local/bin/chromium && chmod 644 /etc/mousse/*
USER sandbox
