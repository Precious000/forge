import re
from typing import Optional
from registry.metadata import get_artifact, get_all_versions

# ── Semver parsing ──────────────────────────────────────────────────────────

def parse_version(v: str) -> tuple[int, int, int]:
    m = re.match(r'^(\d+)\.(\d+)\.(\d+)', v.strip())
    if not m:
        raise ValueError(f"Invalid semver: {v!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))

def version_satisfies(version: str, constraint: str) -> bool:
    constraint = constraint.strip()
    try:
        vt = parse_version(version)
    except ValueError:
        return False

    # Caret: ^1.2.3 → >=1.2.3 <2.0.0
    m = re.match(r'^\^(\d+\.\d+\.\d+)$', constraint)
    if m:
        low = parse_version(m.group(1))
        high = (low[0] + 1, 0, 0)
        return low <= vt < high

    # Tilde: ~1.2.3 → >=1.2.3 <1.3.0
    m = re.match(r'^~(\d+\.\d+\.\d+)$', constraint)
    if m:
        low = parse_version(m.group(1))
        high = (low[0], low[1] + 1, 0)
        return low <= vt < high

    # Range: >=1.0.0 <2.0.0
    m = re.match(r'^(>=|<=|>|<|=)(\d+\.\d+\.\d+)$', constraint)
    if m:
        op, ver = m.group(1), parse_version(m.group(2))
        if op == '>=': return vt >= ver
        if op == '<=': return vt <= ver
        if op == '>':  return vt > ver
        if op == '<':  return vt < ver
        if op == '=':  return vt == ver

    # Compound: >=1.0.0 <2.0.0
    parts = constraint.split()
    if len(parts) > 1:
        return all(version_satisfies(version, p) for p in parts)

    # Exact
    try:
        return parse_version(version) == parse_version(constraint)
    except ValueError:
        return False

def best_version(versions: list[str], constraint: str) -> Optional[str]:
    matching = [v for v in versions if version_satisfies(v, constraint)]
    if not matching:
        return None
    return max(matching, key=parse_version)

# ── Resolver ────────────────────────────────────────────────────────────────

class ResolverError(Exception):
    pass

def resolve(dependencies: list[dict], visited=None, path=None, resolved=None, constraints_map=None):
    """
    Recursively resolves dependencies.
    Returns dict: {name: {version, sha256}}
    """
    if visited is None:
        visited = set()
    if path is None:
        path = []
    if resolved is None:
        resolved = {}
    if constraints_map is None:
        constraints_map = {}

    for dep in dependencies:
        name = dep["name"]
        constraint = dep["version"]

        # Cycle detection
        if name in path:
            cycle = " → ".join(path + [name])
            raise ResolverError(f"Cycle detected: {cycle}")

        all_versions = get_all_versions(name)
        if not all_versions:
            raise ResolverError(f"Package not found in registry: {name!r}")

        chosen = best_version(all_versions, constraint)
        if not chosen:
            raise ResolverError(
                f"No version of {name!r} satisfies constraint {constraint!r}. "
                f"Available: {all_versions}"
            )

        # Conflict detection
        if name in resolved:
            existing_version = resolved[name]["version"]
            existing_constraint = constraints_map[name]
            if existing_version != chosen:
                raise ResolverError(
                    f"Version conflict for {name!r}: "
                    f"path {path} wants {constraint!r} → {chosen}, "
                    f"but already resolved to {existing_version} via constraint {existing_constraint!r}"
                )
        else:
            meta = get_artifact(name, chosen)
            if not meta:
                raise ResolverError(f"Artifact {name}@{chosen} not found in registry")
            resolved[name] = {"version": chosen, "sha256": meta["sha256"]}
            constraints_map[name] = constraint

            # Recurse into transitive deps
            transitive = meta.get("deps", [])
            if transitive:
                resolve(transitive, visited, path + [name], resolved, constraints_map)

    return resolved

def build_lockfile(dependencies: list[dict]) -> dict:
    resolved = resolve(dependencies)
    # Sort for determinism
    locked = {k: resolved[k] for k in sorted(resolved)}
    return {"locked": locked}
