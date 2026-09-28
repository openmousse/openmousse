# The errand Agent's shell sandbox: OpenClaw's default image + a few tools, and Sentinel's CA as the ONLY trusted root.
# Every HTTPS request leaves through Sentinel (the network has no other way out), which re-signs it with this CA,
# so trusting nothing else costs nothing and makes a direct connection fail even if one were possible.
FROM openclaw-sandbox:bookworm-slim
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3-requests \
  && rm -rf /var/lib/apt/lists/*
COPY ca.pem /etc/mousse/sentinel-ca.pem
RUN cp /etc/mousse/sentinel-ca.pem /etc/ssl/certs/ca-certificates.crt \
  && chmod 644 /etc/mousse/sentinel-ca.pem /etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/mousse/sentinel-ca.pem \
    REQUESTS_CA_BUNDLE=/etc/mousse/sentinel-ca.pem \
    CURL_CA_BUNDLE=/etc/mousse/sentinel-ca.pem \
    NODE_EXTRA_CA_CERTS=/etc/mousse/sentinel-ca.pem \
    GIT_SSL_CAINFO=/etc/mousse/sentinel-ca.pem
USER sandbox
