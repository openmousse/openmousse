# OpenMousse in one container: the server with its built-in engine and the memory tree (batch 9, 15.2). Build from the repository root:
#   docker build -f docker/Dockerfile -t openmousse .          or     docker compose -f docker/compose.yaml up -d --build
# Everything that changes lives in the /data volume (docker/entrypoint.py lists what goes where). See docs/docker.md.
# Behind a TLS-inspecting proxy, hand pip its CA without baking it into a layer:
#   docker build --secret id=ca_bundle,src=/path/to/ca.pem -f docker/Dockerfile -t openmousse .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MOUSSE_INSTALL=docker \
    MOUSSE_DATA=/data \
    MOUSSE_SERVER_CONFIG=/data/server.json \
    MOUSSE_TREE_HOME=/data/tree \
    MOUSSE_REPO=/opt/openmousse \
    HOME=/data/home

# The base image already has ca-certificates and tzdata (timezones); nothing else comes from apt.
# Dependencies first, so a code change doesn't reinstall them
COPY server/requirements.txt /opt/openmousse/server/requirements.txt
COPY tree /opt/openmousse/tree
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    pip install -r /opt/openmousse/server/requirements.txt /opt/openmousse/tree

# The rest of the repository (.dockerignore keeps out .git, node_modules and the app's sources; a web build in app/dist comes along)
COPY . /opt/openmousse

RUN useradd --uid 1000 --create-home --home-dir /home/mousse --shell /usr/sbin/nologin mousse \
  && mkdir -p /data \
  && chown mousse:mousse /data \
  && ln -s /opt/openmousse/bin/mousse /usr/local/bin/mousse

USER mousse
WORKDIR /opt/openmousse/server
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=6s --start-period=40s --retries=3 CMD ["python3", "/opt/openmousse/docker/entrypoint.py", "health"]
ENTRYPOINT ["python3", "/opt/openmousse/docker/entrypoint.py"]
