# Forge — CI/CD Platform with Integrated Artifact Registry

## Public URL
- Engine API: http://YOUR_EC2_IP:8000
- Registry API: http://YOUR_EC2_IP:8001

## Pipeline YAML Schema

```yaml
name: build-lib-http          # required: pipeline name
version: 1.0.0                # required: semver pipeline version
dependencies:                 # optional: registry deps pulled before jobs
  - name: lib-core
    version: "^1.0.0"         # semver constraint: exact, ^, ~, or range
jobs:
  build:                      # job name
    runtime: alpine:3.18      # required: Docker image
    resources:
      cpu: 1.0                # CPU cores
      memory: 512Mi           # memory limit
    needs: []                 # optional: other job names this depends on
    steps:
      - name: test
        run: "sh ./test.sh"   # shell command
      - name: package
        run: "tar czf out.tar.gz src/"
artifacts:                    # published automatically after jobs succeed
  - name: lib-http
    version: 1.0.0
    path: ./out.tar.gz
```

## Architecture

### DAG Scheduler
Jobs declare `needs: [other-job]`. The scheduler builds a directed acyclic graph,
detects cycles via Kahn's algorithm (topological sort), groups independent jobs
into parallel waves, and executes each wave concurrently using Python threads.
Failed jobs cause their dependents to be marked `skipped`, not `failed`.

### Isolation
Each job runs in a Docker container with:
- Its own filesystem (workspace bind-mounted, host FS invisible)
- `--network forge_internal` (only registry endpoint reachable)
- CPU/memory limits from YAML via Docker cgroups
- `--read-only` rootfs + `/tmp` tmpfs
- `--security-opt=no-new-privileges` + `--pids-limit=256`

### Storage Layer
Content-addressable: blobs stored under `data/artifacts/blobs/<sha256[:2]>/<sha256>`.
`(name, version)` → SHA-256 mapping stored in SQLite. Immutability enforced via
`UNIQUE(name, version)` constraint — second insert raises 409.

### Dependency Resolver
Implements semver parsing from scratch: caret `^`, tilde `~`, exact, and
comparator ranges (`>=1.0.0 <2.0.0`). Walks transitive deps via registry metadata.
Determinism guaranteed by: (1) always selecting the highest satisfying version,
(2) sorting the lockfile keys alphabetically before serializing.

### Log Streaming
Each job writes timestamped lines to `data/logs/<run_id>/<job>.log` via append.
SSE endpoint tails log files using async file reads with 100ms polling.
A 50MB log streams line-by-line without loading into memory.

## Concurrent Publish Safety
Two pipelines racing to publish the same `(name, version)` hit SQLite's
`UNIQUE` constraint. The second one gets a 409 response. SQLite's
write serialization ensures only one succeeds.

## Fresh VPS Setup

```bash
# 1. Install dependencies
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin python3-pip
sudo usermod -aG docker $USER && newgrp docker

# 2. Clone/copy project
cd ~/forge

# 3. Build and start
mkdir -p data/{artifacts,db,logs}
docker compose build
docker compose up -d

# 4. Create first auth token
docker compose exec registry python3 -c "
import sys, os; os.environ['TOKEN_DB_PATH']='/data/db/tokens.db'
sys.path.insert(0,'/app')
from registry.auth import create_token
print('FORGE_TOKEN=' + create_token('admin'))
"

# 5. Set token in env
export FORGE_TOKEN=<token from above>
echo "FORGE_TOKEN=$FORGE_TOKEN" > .env
docker compose down && docker compose up -d

# 6. Install CLI
pip3 install click requests pyyaml --break-system-packages
sudo ln -sf ~/forge/cli/forge.py /usr/local/bin/forge
forge login http://YOUR_IP:8000 --token $FORGE_TOKEN
```
EOF

PHASE 12: Final Verification Checklist
Run this to verify everything is live:
bash# Services running?
docker compose ps

# Registry health
curl http://localhost:8001/artifacts/nonexistent
# Should return 404 JSON, not connection refused

# Engine health
curl http://localhost:8000/runs/nonexistent
# Should return 404 JSON

# Your public IP
curl ifconfig.me

# Tail all logs
docker compose logs -f
