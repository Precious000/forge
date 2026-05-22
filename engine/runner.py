import os
import subprocess
import threading
import hashlib
import tempfile
import shutil
import requests
from pathlib import Path
from engine.logs import write_log_line

REGISTRY_URL = os.environ.get("REGISTRY_URL", "http://registry:8001")
FORGE_TOKEN = os.environ.get("FORGE_TOKEN", "")

def _pull_dependency(name: str, version: str, sha256: str, dest_dir: Path):
    url = f"{REGISTRY_URL}/artifacts/{name}/{version}"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    data = resp.content

    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != sha256:
        raise ValueError(
            f"INTEGRITY FAILURE pulling {name}@{version}: "
            f"expected={sha256} actual={actual_sha}"
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = dest_dir / f"{name}-{version}.tar.gz"
    artifact_path.write_bytes(data)

def run_job(run_id: str, job_name: str, job_def: dict, lockfile: dict,
            workspace: Path, resources: dict = None) -> bool:
    """
    Runs a single job inside a Docker container with:
    - isolated filesystem (bind-mount workspace only)
    - no network except registry
    - CPU/memory limits from YAML
    - PID namespace isolation
    """
    write_log_line(run_id, job_name, f"=== Starting job: {job_name} ===")

    runtime = job_def.get("runtime", "alpine:3.18")
    res = job_def.get("resources", {})
    cpu_limit = str(res.get("cpu", 1.0))
    mem_limit = res.get("memory", "512m").replace("Mi", "m").replace("MB", "m")

    # Pull dependencies into workspace/deps/
    locked = lockfile.get("locked", {})
    for dep_name, dep_info in locked.items():
        dep_dest = workspace / "deps" / dep_name
        write_log_line(run_id, job_name, f"Pulling dep: {dep_name}@{dep_info['version']}")
        try:
            _pull_dependency(dep_name, dep_info["version"], dep_info["sha256"], dep_dest)
        except ValueError as e:
            write_log_line(run_id, job_name, f"INTEGRITY FAILURE: {e}")
            return False, "integrity_failure"

    # Build Docker command
    cmd = [
        "docker", "run", "--rm",
        "--name", f"forge-{run_id}-{job_name}",
        "--network", "forge_internal",  # only registry reachable
        f"--cpus={cpu_limit}",
        f"--memory={mem_limit}",
        "--pids-limit=256",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
        "-v", f"{workspace}:/workspace:rw",
        "-w", "/workspace",
        "-e", f"FORGE_TOKEN={FORGE_TOKEN}",
        "-e", f"FORGE_URL={REGISTRY_URL}",
        runtime,
        "sh", "-c", " && ".join(
            step["run"] for step in job_def.get("steps", [])
        )
    ]

    write_log_line(run_id, job_name, f"Running in container: {runtime}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        for line in iter(proc.stdout.readline, ""):
            write_log_line(run_id, job_name, line.rstrip())

        proc.wait(timeout=int(os.environ.get("JOB_TIMEOUT", 1800)))

        if proc.returncode == 0:
            write_log_line(run_id, job_name, f"=== Job {job_name} SUCCEEDED ===")
            return True, "succeeded"
        else:
            write_log_line(run_id, job_name, f"=== Job {job_name} FAILED (exit {proc.returncode}) ===")
            return False, "failed"

    except subprocess.TimeoutExpired:
        proc.kill()
        write_log_line(run_id, job_name, f"=== Job {job_name} TIMED OUT ===")
        return False, "failed"
    except Exception as e:
        write_log_line(run_id, job_name, f"=== Job {job_name} ERROR: {e} ===")
        return False, "failed"

def publish_artifacts(run_id: str, job_name: str, artifacts: list,
                      workspace: Path, pipeline_deps: list):
    """Auto-publish declared artifacts after successful job."""
    for art in artifacts:
        art_path = workspace / art["path"].lstrip("./")
        if not art_path.exists():
            write_log_line(run_id, job_name, f"WARNING: artifact path not found: {art_path}")
            continue

        data = art_path.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()

        import json
        resp = requests.post(
            f"{REGISTRY_URL}/artifacts/{art['name']}/{art['version']}",
            headers={"Authorization": f"Bearer {FORGE_TOKEN}"},
            files={"file": (art_path.name, data, "application/octet-stream")},
            data={
                "checksum": f"sha256:{sha256}",
                "deps": json.dumps(pipeline_deps)
            },
            timeout=120
        )

        if resp.status_code == 201:
            write_log_line(run_id, job_name, f"Published {art['name']}@{art['version']} sha256:{sha256}")
        elif resp.status_code == 409:
            write_log_line(run_id, job_name, f"SKIP: {art['name']}@{art['version']} already exists")
        else:
            write_log_line(run_id, job_name, f"PUBLISH FAILED {art['name']}@{art['version']}: {resp.text}")
