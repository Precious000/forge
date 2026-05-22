import yaml
import re

KNOWN_TOP_KEYS = {"name", "version", "dependencies", "jobs", "artifacts"}
KNOWN_JOB_KEYS = {"runtime", "resources", "steps", "needs"}
KNOWN_STEP_KEYS = {"name", "run"}
KNOWN_ARTIFACT_KEYS = {"name", "version", "path"}

class ParseError(Exception):
    pass

def _check_unknown(d: dict, known: set, context: str):
    for key in d:
        if key not in known:
            raise ParseError(f"Unknown field '{key}' in {context}")

def parse_pipeline(content: str) -> dict:
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise ParseError(f"YAML parse error: {e}")

    if not isinstance(data, dict):
        raise ParseError("Pipeline must be a YAML mapping")

    _check_unknown(data, KNOWN_TOP_KEYS, "pipeline root")

    for required in ("name", "version", "jobs"):
        if required not in data:
            raise ParseError(f"Missing required field: '{required}'")

    if not re.match(r'^\d+\.\d+\.\d+', str(data["version"])):
        raise ParseError(f"Pipeline version must be semver, got: {data['version']!r}")

    jobs = data.get("jobs", {})
    if not isinstance(jobs, dict) or not jobs:
        raise ParseError("'jobs' must be a non-empty mapping")

    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            raise ParseError(f"Job '{job_name}' must be a mapping")
        _check_unknown(job, KNOWN_JOB_KEYS, f"job '{job_name}'")
        if "runtime" not in job:
            raise ParseError(f"Job '{job_name}' missing required field 'runtime'")
        if "steps" not in job:
            raise ParseError(f"Job '{job_name}' missing required field 'steps'")
        for i, step in enumerate(job["steps"]):
            _check_unknown(step, KNOWN_STEP_KEYS, f"job '{job_name}' step {i}")
            if "run" not in step:
                raise ParseError(f"Job '{job_name}' step {i} missing 'run'")

    for i, art in enumerate(data.get("artifacts", [])):
        _check_unknown(art, KNOWN_ARTIFACT_KEYS, f"artifact {i}")
        for req in ("name", "version", "path"):
            if req not in art:
                raise ParseError(f"Artifact {i} missing required field '{req}'")

    deps = data.get("dependencies", [])
    for dep in deps:
        if "name" not in dep or "version" not in dep:
            raise ParseError(f"Dependency entry missing 'name' or 'version': {dep}")

    return data
