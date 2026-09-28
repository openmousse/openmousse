#!/bin/sh
# Stands in for /usr/bin/chromium (earlier on PATH). OpenClaw's entrypoint starts `chromium …`; this adds:
#  - --proxy-server: everything goes to Sentinel, loopback too (<-loopback>), so a page can't reach the container's
#    own CDP port behind Sentinel's back; no proxy configured = refuse to start (fail closed)
#  - --ignore-certificate-errors-spki-list: Sentinel re-signs every site with its own CA; trust that key only
#  - WebRTC may not open its own UDP (there is no route anyway)
#  - no background services (component updates, sync, push registration, optimisation hints…): only what the errand opens goes out
PROXY="${HTTPS_PROXY:-${https_proxy:-}}"
if [ -z "$PROXY" ]; then
  echo "chromium-sentinel: no HTTPS_PROXY set, refusing to start without Sentinel" >&2
  exit 1
fi
SPKI="$(cat /etc/mousse/sentinel-ca.spki 2>/dev/null)"
exec /usr/bin/chromium \
  --proxy-server="$PROXY" \
  --proxy-bypass-list="<-loopback>" \
  --ignore-certificate-errors-spki-list="$SPKI" \
  --force-webrtc-ip-handling-policy=disable_non_proxied_udp \
  --disable-quic \
  --disable-component-update --disable-sync --no-pings --disable-domain-reliability \
  --disable-client-side-phishing-detection --disable-default-apps --no-service-autorun \
  --disable-features=OptimizationHints,OptimizationHintsFetching,OptimizationGuideModelDownloading,MediaRouter,DialMediaRouteProvider,AutofillServerCommunication,Translate,InterestFeedContentSuggestions,CertificateTransparencyComponentUpdater,PushMessaging,GCMUseDedicatedNetworkThread \
  "$@"
