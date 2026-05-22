from collections import defaultdict, deque

class CycleError(Exception):
    pass

def build_dag(jobs: dict) -> dict:
    """Returns adjacency list: job -> list of jobs that depend on it."""
    graph = defaultdict(list)
    in_degree = {name: 0 for name in jobs}

    for job_name, job_def in jobs.items():
        needs = job_def.get("needs", [])
        for dep in needs:
            if dep not in jobs:
                raise ValueError(f"Job '{job_name}' depends on unknown job '{dep}'")
            graph[dep].append(job_name)
            in_degree[job_name] += 1

    return graph, in_degree

def topological_sort(jobs: dict) -> list[list[str]]:
    """
    Returns list of waves. Each wave is a list of job names
    that can execute in parallel.
    Raises CycleError if a cycle is detected.
    """
    graph, in_degree = build_dag(jobs)

    queue = deque([name for name, deg in in_degree.items() if deg == 0])
    waves = []
    visited_count = 0

    while queue:
        wave = list(queue)
        queue.clear()
        waves.append(wave)
        visited_count += len(wave)

        next_wave_candidates = set()
        for job_name in wave:
            for dependent in graph[job_name]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    next_wave_candidates.add(dependent)

        for candidate in next_wave_candidates:
            queue.append(candidate)

    if visited_count != len(jobs):
        remaining = [j for j in jobs if in_degree[j] > 0]
        raise CycleError(f"Cycle detected among jobs: {remaining}")

    return waves
