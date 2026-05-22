import os
import asyncio
import aiofiles
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = os.environ.get("LOG_DIR", "/data/logs")

def log_path(run_id: str, job_name: str) -> Path:
    p = Path(LOG_DIR) / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{job_name}.log"

def write_log_line(run_id: str, job_name: str, line: str):
    ts = datetime.now(timezone.utc).isoformat()
    entry = f"{ts} [{job_name}] {line}\n"
    path = log_path(run_id, job_name)
    with open(path, "a") as f:
        f.write(entry)
    return entry

async def stream_log_file(run_id: str, job_name: str):
    """Async generator: yields existing lines then tails for new ones."""
    path = log_path(run_id, job_name)
    path.touch()
    async with aiofiles.open(path, "r") as f:
        # Send backlog
        while True:
            line = await f.readline()
            if line:
                yield line.rstrip("\n")
            else:
                break
        # Tail
        while True:
            line = await f.readline()
            if line:
                yield line.rstrip("\n")
            else:
                await asyncio.sleep(0.1)

async def stream_all_logs(run_id: str, jobs: list[str], is_running_fn):
    """Yields all log lines from all jobs for a run, tagged by job."""
    path = Path(LOG_DIR) / run_id
    path.mkdir(parents=True, exist_ok=True)
    seen = {j: 0 for j in jobs}

    while True:
        any_new = False
        for job in jobs:
            jpath = log_path(run_id, job)
            if not jpath.exists():
                continue
            with open(jpath, "r") as f:
                lines = f.readlines()
            for line in lines[seen[job]:]:
                seen[job] += 1
                yield line.rstrip("\n")
                any_new = True

        if not is_running_fn() and not any_new:
            break

        if not any_new:
            await asyncio.sleep(0.1)
