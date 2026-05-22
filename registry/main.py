import os
import hashlib
from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form
from fastapi.responses import Response, JSONResponse
from registry.auth import require_auth
from registry.storage import store_blob, get_blob, verify_checksum
from registry.metadata import insert_artifact, get_artifact, list_versions

app = FastAPI(title="Forge Registry")

def _auth(authorization: str = None):
    try:
        return require_auth(authorization)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

@app.post("/artifacts/{name}/{version}", status_code=201)
async def publish_artifact(
    name: str,
    version: str,
    file: UploadFile = File(...),
    checksum: str = Form(...),
    deps: str = Form("[]"),
    authorization: str = Header(None)
):
    publisher = _auth(authorization)

    # Validate semver
    import re
    if not re.match(r'^\d+\.\d+\.\d+', version):
        raise HTTPException(status_code=400, detail=f"Invalid semver version: {version}")

    # Check immutability
    existing = get_artifact(name, version)
    if existing:
        raise HTTPException(status_code=409, detail=f"{name}@{version} already exists (immutable)")

    data = await file.read()

    # Verify checksum
    if not verify_checksum(data, checksum):
        actual = hashlib.sha256(data).hexdigest()
        raise HTTPException(
            status_code=400,
            detail=f"Checksum mismatch: declared={checksum}, actual=sha256:{actual}"
        )

    sha256 = store_blob(data)

    import json
    try:
        dep_list = json.loads(deps)
    except Exception:
        dep_list = []

    try:
        insert_artifact(name, version, sha256, len(data), publisher, dep_list)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {"name": name, "version": version, "sha256": sha256, "size": len(data)}

@app.get("/artifacts/{name}/{version}/meta")
def artifact_meta(name: str, version: str):
    meta = get_artifact(name, version)
    if not meta:
        raise HTTPException(status_code=404, detail=f"{name}@{version} not found")
    return meta

@app.get("/artifacts/{name}/{version}")
def download_artifact(name: str, version: str):
    meta = get_artifact(name, version)
    if not meta:
        raise HTTPException(status_code=404, detail=f"{name}@{version} not found")
    data = get_blob(meta["sha256"])
    if not data:
        raise HTTPException(status_code=404, detail="Blob not found")
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"X-Artifact-SHA256": meta["sha256"]}
    )

@app.get("/artifacts/{name}")
def list_artifact_versions(name: str):
    versions = list_versions(name)
    return {"name": name, "versions": versions}
