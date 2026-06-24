from collections import defaultdict
from datetime import datetime, timezone

from scheduler_manifest import JOB_GRAPH, _DYNAMIC_ETF_RE, _resolve_manifest, job_label

_OVERLAP_BUFFER_MIN = 2
_UNKNOWN_GAP_MIN = 30
_WEEK_MIN = 7 * 24 * 60
_BACKWARDS_FOLLOW_MIN = 240
_BACKWARDS_STALE_MIN = 24 * 60
_WEEKDAY_TO_INT = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _fire_times(schedule: dict | None) -> list[int]:
    """Minute-of-week slots a cron job fires at; empty for interval/non-cron triggers."""
    if not schedule:
        return []
    return [wd * 1440 + schedule["minute_of_day"] for wd in schedule["weekdays"]]


def _wd_to_int(token: str) -> int | None:
    token = token.strip().lower()
    if token in _WEEKDAY_TO_INT:
        return _WEEKDAY_TO_INT[token]
    if token.isdigit():
        return int(token) % 7
    return None


def _weekdays_from_expr(expr: str) -> set[int]:
    expr = expr.strip().lower()
    if expr in ("*", "?", ""):
        return set(range(7))
    days: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if "-" in part:
            a, _, b = part.partition("-")
            ai, bi = _wd_to_int(a), _wd_to_int(b)
            if ai is not None and bi is not None:
                days.update(range(ai, bi + 1) if ai <= bi else list(range(ai, 7)) + list(range(0, bi + 1)))
        else:
            wi = _wd_to_int(part)
            if wi is not None:
                days.add(wi)
    return days or set(range(7))


def _first_int_from_expr(expr: str, default: int = 0) -> int:
    token = expr.strip().split(",")[0].split("/")[0].split("-")[0]
    if token in ("*", "?", ""):
        return default
    try:
        return int(token)
    except ValueError:
        return default


def _schedule_slot(trigger) -> tuple[set[int], int] | None:
    try:
        fields = {f.name: str(f) for f in trigger.fields}
    except AttributeError:
        return None
    day_expr = fields.get("day", "*").strip()
    if day_expr not in ("*", "?", ""):
        return None
    hour = _first_int_from_expr(fields.get("hour", "0"))
    minute = _first_int_from_expr(fields.get("minute", "0"))
    return _weekdays_from_expr(fields.get("day_of_week", "*")), hour * 60 + minute


def _period_days(weekdays: set[int] | None) -> int:
    return 1 if (weekdays is None or len(weekdays) >= 5) else 7


def _parse_last_run(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _job_status(node: dict) -> tuple[str, str]:
    if not node["enabled"]:
        return "disabled", "disabled"
    if node.get("last_status") == "error":
        return "red", "error"
    last_run = _parse_last_run(node.get("last_run"))
    if last_run is None:
        return "amber", "never_run"
    if not node.get("schedule"):
        return "green", "ok"
    weekdays = set(node["schedule"]["weekdays"])
    period = _period_days(weekdays)
    age_days = (datetime.now(timezone.utc) - last_run).total_seconds() / 86400.0
    if age_days > period * 2 + 2:
        return "red", "overdue"
    if age_days > period + 1:
        return "amber", "stale"
    return "green", "ok"


def _build_node(job_id: str, meta: dict, job, run_row: dict) -> dict:
    if meta.get("category") == "external":
        return {
            "id": job_id,
            "label": meta["label"],
            "category": "external",
            "engine": meta["engine"],
            "produces": list(meta.get("produces", [])),
            "consumes": [],
            "enabled": True,
            "last_run": None,
            "last_status": None,
            "avg_duration_sec": None,
            "next_run": None,
            "schedule": None,
            "status": "external",
            "status_reason": "External data source — not a scheduled job",
            "settings_anchor": meta.get("settings_anchor"),
        }
    enabled = job is not None
    schedule = None
    if enabled:
        slot = _schedule_slot(job.trigger)
        if slot is not None:
            weekdays, minute_of_day = slot
            schedule = {"weekdays": sorted(weekdays), "minute_of_day": minute_of_day}
    label = meta["label"]
    if _DYNAMIC_ETF_RE.match(job_id):
        cfg_id = job_id.split("_")[2]
        phase = "pre-open" if job_id.endswith("pre_job") else "post-close"
        label = f"ETF Price Predictor #{cfg_id} ({phase})"
    runs = run_row or {}
    next_run = None
    next_run_time = getattr(job, "next_run_time", None) if enabled else None
    if next_run_time is not None:
        next_run = next_run_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    node = {
        "id": job_id,
        "label": label,
        "category": meta["category"],
        "engine": meta["engine"],
        "produces": list(meta.get("produces", [])),
        "consumes": list(meta.get("consumes", [])),
        "enabled": enabled,
        "last_run": runs.get("last_run"),
        "last_status": runs.get("last_status"),
        "avg_duration_sec": runs.get("avg_duration_sec"),
        "next_run": next_run,
        "schedule": schedule,
        "settings_anchor": meta.get("settings_anchor"),
    }
    node["status"], node["status_reason"] = _job_status(node)
    return node


def build_workflow_graph() -> dict:
    from scheduler_engine import scheduler, get_all_job_last_runs
    runs = get_all_job_last_runs()
    live = {j.id: j for j in scheduler.get_jobs()}
    nodes, seen = [], set()
    for job_id, meta in JOB_GRAPH.items():
        if meta.get("dynamic"):
            continue
        nodes.append(_build_node(job_id, meta, live.get(job_id), runs.get(job_id, {})))
        seen.add(job_id)
    for job_id, job in live.items():
        if job_id in seen:
            continue
        meta = _resolve_manifest(job_id)
        if meta is not None:
            nodes.append(_build_node(job_id, meta, job, runs.get(job_id, {})))
    edges = _derive_edges(nodes)
    return {"nodes": nodes, "edges": edges}


def _derive_edges(nodes: list[dict]) -> list[dict]:
    producers = defaultdict(list)
    for n in nodes:
        for artifact in n["produces"]:
            producers[artifact].append(n["id"])
    edges, seen = [], set()
    for n in nodes:
        for artifact in n["consumes"]:
            for producer_id in producers.get(artifact, []):
                if producer_id == n["id"]:
                    continue
                key = (producer_id, n["id"], artifact)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"from": producer_id, "to": n["id"], "via": artifact})
    return edges


def detect_workflow_conflicts(graph: dict) -> list[dict]:
    nodes = {n["id"]: n for n in graph["nodes"]}
    enabled_producers: dict[str, list[str]] = defaultdict(list)
    for n in graph["nodes"]:
        if n["enabled"]:
            for artifact in n["produces"]:
                enabled_producers[artifact].append(n["id"])
    conflicts = []
    for edge in graph["edges"]:
        producer, consumer = nodes.get(edge["from"]), nodes.get(edge["to"])
        if not producer or not consumer:
            continue
        if not consumer["enabled"]:
            continue
        if not producer["enabled"]:
            if enabled_producers.get(edge["via"]):
                continue
            conflicts.append({
                "type": "disabled_upstream", "severity": "warning",
                "job_id": consumer["id"], "related": producer["id"],
                "message": f"{consumer['label']} depends on {producer['label']} (via {edge['via']}), which is disabled — its inputs may be stale or missing.",
            })
            continue
        p_fires, c_fires = _fire_times(producer.get("schedule")), _fire_times(consumer.get("schedule"))
        if not p_fires or not c_fires:
            continue
        back_gap = min((cf - pf) % _WEEK_MIN for cf in c_fires for pf in p_fires)
        fwd_gap = min((pf - cf) % _WEEK_MIN for cf in c_fires for pf in p_fires)
        avg = producer.get("avg_duration_sec")
        if avg is None:
            if back_gap < _UNKNOWN_GAP_MIN:
                conflicts.append({
                    "type": "overlap_risk", "severity": "info",
                    "job_id": consumer["id"], "related": producer["id"],
                    "message": f"{consumer['label']} starts {back_gap} min after {producer['label']} (its source of {edge['via']}); the producer's typical runtime is not yet known, so overlap cannot be ruled out.",
                })
        elif back_gap < avg / 60.0 + _OVERLAP_BUFFER_MIN:
            conflicts.append({
                "type": "overlap_risk", "severity": "warning",
                "job_id": consumer["id"], "related": producer["id"],
                "message": f"{consumer['label']} starts {back_gap} min after {producer['label']} (its source of {edge['via']}), but {producer['label']} typically runs ~{avg / 60.0:.0f} min — it may still be running, so {consumer['label']} could read incomplete data.",
            })
        if fwd_gap <= _BACKWARDS_FOLLOW_MIN and back_gap >= _BACKWARDS_STALE_MIN:
            conflicts.append({
                "type": "backwards_ordering", "severity": "critical",
                "job_id": consumer["id"], "related": producer["id"],
                "message": f"{consumer['label']} runs {fwd_gap} min before {producer['label']}, the upstream producer of {edge['via']} — it cannot use the same cycle's output and falls back on data at least {back_gap // 60}h old.",
            })
    for node in graph["nodes"]:
        reason = node.get("status_reason")
        if reason == "error":
            conflicts.append({
                "type": "last_run_error", "severity": "critical",
                "job_id": node["id"], "related": None,
                "message": f"{node['label']} failed on its last run.",
            })
        elif reason == "overdue":
            conflicts.append({
                "type": "stale_never_run", "severity": "warning",
                "job_id": node["id"], "related": None,
                "message": f"{node['label']} is enabled but has not run recently (last run: {node.get('last_run') or 'never'}).",
            })
        elif reason == "never_run":
            conflicts.append({
                "type": "stale_never_run", "severity": "info",
                "job_id": node["id"], "related": None,
                "message": f"{node['label']} is enabled and scheduled but has never recorded a run.",
            })
    return conflicts
