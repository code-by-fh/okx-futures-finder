#!/usr/bin/env bash
set -euo pipefail

image="${1:-okx-futures-finder:smoke}"
name="okx-finder-smoke-${RANDOM}-${RANDOM}"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT

# No OKX access required: the container must serve the UI during API outages too.
docker run --detach --network none --name "$name" "$image" >/dev/null
ready=false
for attempt in {1..15}; do
  if docker exec "$name" python -c "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8765/healthz',timeout=2))['status']=='ok'" 2>/dev/null; then
    ready=true
    break
  fi
  sleep 1
done
if [ "$ready" != true ]; then
  docker logs "$name"
  echo 'Container did not become HTTP-ready' >&2
  exit 1
fi
docker exec "$name" python -c "import json,os,pathlib,urllib.request; assert os.getuid()==10001; c=json.loads(pathlib.Path('/app/config.json').read_text()); assert 'pushover' not in c; assert 'strategy' not in c; s=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status')); assert isinstance(s['pairs'],list); assert not pathlib.Path('/app/state.json').exists(); assert b'ATR(14)' in urllib.request.urlopen('http://127.0.0.1:8765/').read(); assert urllib.request.urlopen('http://127.0.0.1:8765/dashboard.js').status==200"
echo 'Container smoke test passed: non-root, public config, health, UI, assets and status API.'
