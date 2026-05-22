import os
import uuid
import asyncio
import json
import threading
import tempfile
import shutil
import httpx
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, UploadFile, File, Header, HTTPException
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from engine.parser import parse_pipeline, ParseError
from engine.scheduler import topological_sort, CycleError
from engine.logs import stream_all_logs, write_log_line, log_path
from engine.runner import run_job, publish_artifacts

REGISTRY_URL = os.environ.get("REGISTRY_URL", "http://registry:8001")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK", "")
FORGE_TOKEN = os.environ.get("FORGE_TOKEN", "")

app = FastAPI(title="Forge Engine")

# In-memory run state (survives for process lifetime)
RUNS: dict = {}

def _notify_slack(message: str):
    if not SLACK_WEBHOOK:
        return
    try:
        import requests
        requests.post(SLACK_WEBHOOK, json={"text": message}, timeout=5)
    except Exception:
        pass

def _resolve_deps(dependencies: list) -> tuple[dict, str | None]:
    """Call registry resolver. Returns (lockfile, error_message)."""
    if not dependencies:
        return {"locked": {}}, None
    try:
        import sys
        sys.path.insert(0, "/app")
        from registry.resolver import build_lockfile, ResolverError
        lockfile = build_lockfile(dependencies)
        return lockfile, None
    except Exception as e:
        return {}, str(e)

def _run_pipeline_thread(run_id: str, pipeline: dict):
    run = RUNS[run_id]
    run["status"] = "running"
    run["started_at"] = datetime.now(timezone.utc).isoformat()
    pipeline_name = pipeline.get("name", run_id)

    _notify_slack(f":rocket: Pipeline *{pipeline_name}* started | run_id={run_id}")

    # 1. Resolve dependencies
    deps = pipeline.get("dependencies", [])
    lockfile, err = _resolve_deps(deps)

    if err:
        run["status"] = "conflict_failure" if "conflict" in err.lower() else "cycle_failure"
        run["error"] = err
        write_log_line(run_id, "resolver", f"RESOLUTION FAILED: {err}")
        _notify_slack(
            f":x: Resolution failure in *{pipeline_name}* | run_id={run_id}\n```{err}```"
        )
        return

    run["lockfile"] = lockfile

    # 2. Build DAG and detect cycles
    jobs = pipeline.get("jobs", {})
    try:
        waves = topological_sort(jobs)
    except CycleError as e:
        run["status"] = "cycle_failure"
        run["error"] = str(e)
        _notify_slack(f":x: Cycle in *{pipeline_name}*: {e} | run_id={run_id}")
        return

    # 3. Execute waves (parallel within each wave)
    workspace_base = Path(os.environ.get("WORKSPACE_BASE", "/tmp/forge-workspaces"))
    workspace = workspace_base / run_id
    workspace.mkdir(parents=True, exist_ok=True)

    failed_jobs = set()
    overall_status = "succeeded"
    all_job_names = list(jobs.keys())
    run["jobs"] = {j: "queued" for j in all_job_names}

    try:
        for wave in waves:
            threads = []
            results = {}

            def exec_job(jname):
                if jname in failed_jobs:
                    run["jobs"][jname] = "skipped"
                    return

                # Check if any dependency failed
                needs = jobs[jname].get("needs", [])
                if any(n in failed_jobs for n in needs):
                    run["jobs"][jname] = "skipped"
                    failed_jobs.add(jname)
                    return

                run["jobs"][jname] = "running"
                ok, status = run_job(run_id, jname, jobs[jname], lockfile, workspace)
                run["jobs"][jname] = status
                results[jname] = ok
                if not ok:
                    failed_jobs.add(jname)

            for jname in wave:
                t = threading.Thread(target=exec_job, args=(jname,))
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

        if failed_jobs:
            # Check for integrity failure
            for jname in failed_jobs:
                if run["jobs"].get(jname) == "integrity_failure":
                    overall_status = "integrity_failure"
                    break
            else:
                overall_status = "failed"
        else:
            # Publish artifacts
            artifacts = pipeline.get("artifacts", [])
            if artifacts:
                publish_artifacts(run_id, "publisher", artifacts, workspace,
                                  pipeline.get("dependencies", []))

    finally:
        # Cleanup workspace
        try:
            shutil.rmtree(workspace, ignore_errors=True)
        except Exception:
            pass

    run["status"] = overall_status
    run["finished_at"] = datetime.now(timezone.utc).isoformat()

    duration = "N/A"
    try:
        start = datetime.fromisoformat(run["started_at"])
        end = datetime.fromisoformat(run["finished_at"])
        duration = f"{(end - start).total_seconds():.1f}s"
    except Exception:
        pass

    if overall_status == "succeeded":
        _notify_slack(f":white_check_mark: Pipeline *{pipeline_name}* succeeded | run_id={run_id} | duration={duration}")
    else:
        failing = ", ".join(failed_jobs) if failed_jobs else "unknown"
        _notify_slack(
            f":x: Pipeline *{pipeline_name}* {overall_status} | run_id={run_id} | "
            f"duration={duration} | failing_jobs={failing}"
        )


# ── HTTP endpoints ──────────────────────────────────────────────────────────

@app.post("/runs")
async def create_run(pipeline: UploadFile = File(...)):
    content = await pipeline.read()
    try:
        parsed = parse_pipeline(content.decode())
    except ParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    run_id = str(uuid.uuid4())
    RUNS[run_id] = {
        "id": run_id,
        "status": "queued",
        "jobs": {},
        "lockfile": {},
        "pipeline": parsed,
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    t = threading.Thread(target=_run_pipeline_thread, args=(run_id, parsed), daemon=True)
    t.start()

    return {"run_id": run_id}

@app.get("/runs/{run_id}")
def get_run(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "id": run_id,
        "status": run["status"],
        "jobs": run["jobs"],
        "lockfile_url": f"/runs/{run_id}/lockfile",
        "created_at": run.get("created_at"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "error": run.get("error")
    }

@app.get("/runs/{run_id}/lockfile")
def get_lockfile(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.get("lockfile", {})

@app.get("/runs/{run_id}/logs")
async def get_logs(run_id: str, follow: bool = False):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    job_names = list(run.get("jobs", {}).keys()) or ["resolver"]

    def is_running():
        return RUNS.get(run_id, {}).get("status") in ("queued", "running")

    async def event_gen():
        async for line in stream_all_logs(run_id, job_names, is_running):
            yield {"data": line}
            if not follow and not is_running():
                break

    return EventSourceResponse(event_gen())
