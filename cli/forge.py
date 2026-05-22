#!/usr/bin/env python3
import click
import requests
import json
import os
import hashlib
import sys
from pathlib import Path

CONFIG_FILE = Path.home() / ".forge" / "config.json"

def _load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}

def _save_config(cfg):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

def _get_base(cfg=None):
    cfg = cfg or _load_config()
    url = cfg.get("url", os.environ.get("FORGE_URL", "http://localhost:8000"))
    return url.rstrip("/")

def _get_token(cfg=None):
    cfg = cfg or _load_config()
    return cfg.get("token", os.environ.get("FORGE_TOKEN", ""))

@click.group()
def cli():
    """Forge CI/CD platform CLI"""
    pass

@cli.command()
@click.argument("url")
@click.option("--token", prompt=True, hide_input=True)
def login(url, token):
    """Store credentials for a Forge server."""
    cfg = {"url": url.rstrip("/"), "token": token}
    _save_config(cfg)
    click.echo(f"Logged in to {url}")

@cli.command("run")
@click.argument("pipeline_file", type=click.Path(exists=True))
def run_pipeline(pipeline_file):
    """Submit a pipeline for execution."""
    cfg = _load_config()
    base = _get_base(cfg)
    token = _get_token(cfg)
    with open(pipeline_file, "rb") as f:
        resp = requests.post(
            f"{base}/runs",
            files={"pipeline": f},
            headers={"Authorization": f"Bearer {token}"}
        )
    if resp.status_code == 200:
        data = resp.json()
        click.echo(f"Run started: {data['run_id']}")
    else:
        click.echo(f"Error: {resp.status_code} {resp.text}", err=True)
        sys.exit(1)

@cli.command()
@click.argument("run_id")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
def logs(run_id, follow):
    """Fetch logs for a run."""
    cfg = _load_config()
    base = _get_base(cfg)
    url = f"{base}/runs/{run_id}/logs"
    if follow:
        url += "?follow=true"
    with requests.get(url, stream=True) as resp:
        for line in resp.iter_lines():
            if line:
                text = line.decode() if isinstance(line, bytes) else line
                if text.startswith("data:"):
                    click.echo(text[5:].strip())

@cli.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--name", required=True)
@click.option("--version", required=True)
def publish(path, name, version):
    """Publish an artifact to the registry."""
    cfg = _load_config()
    base = _get_base(cfg)
    token = _get_token(cfg)

    # Use registry port
    reg_base = base.replace(":8000", ":8001")

    data = Path(path).read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()

    with open(path, "rb") as f:
        resp = requests.post(
            f"{reg_base}/artifacts/{name}/{version}",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": f},
            data={"checksum": f"sha256:{sha256}"}
        )
    if resp.status_code == 201:
        click.echo(f"Published {name}@{version} sha256:{sha256}")
    else:
        click.echo(f"Error: {resp.status_code} {resp.text}", err=True)
        sys.exit(1)

@cli.command()
@click.argument("pipeline_file", type=click.Path(exists=True))
def resolve(pipeline_file):
    """Print the lockfile for a pipeline without running it."""
    import yaml
    with open(pipeline_file) as f:
        pipeline = yaml.safe_load(f)

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from registry.resolver import build_lockfile, ResolverError
    try:
        lockfile = build_lockfile(pipeline.get("dependencies", []))
        click.echo(json.dumps(lockfile, indent=2))
    except ResolverError as e:
        click.echo(f"Resolution failed: {e}", err=True)
        sys.exit(1)

@cli.command("ls")
@click.argument("package")
def list_versions(package):
    """List versions of a package in the registry."""
    cfg = _load_config()
    base = _get_base(cfg).replace(":8000", ":8001")
    resp = requests.get(f"{base}/artifacts/{package}")
    if resp.status_code == 200:
        data = resp.json()
        for v in data.get("versions", []):
            click.echo(f"{v['version']}  sha256:{v['sha256']}  {v['published_at']}")
    else:
        click.echo(f"Error: {resp.status_code} {resp.text}", err=True)
        sys.exit(1)

if __name__ == "__main__":
    cli()
