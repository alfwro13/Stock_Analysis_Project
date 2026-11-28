from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from accounts.account_reminders_engine import reminder_job_display_name
from db.account_store import get_account
from scheduler_manifest import JOB_GRAPH, _DYNAMIC_ETF_RE, _DYNAMIC_ACCOUNT_SCRAPER_RE, _DYNAMIC_ACCOUNT_TOPUP_RE, _DYNAMIC_ACCOUNT_REMINDER_RE, _resolve_manifest, job_label
from constants import SCHEDULER_RUN_HISTORY_LIMIT
from time_engine import get_user_tz, now_local, parse_stored_utc, to_local

_ACCOUNT_TYPE_ARTIFACT = {"Pension": "pension_account_data", "House": "house_account_data"}

_OVERLAP_BUFFER_MIN = 2
_UNKNOWN_GAP_MIN = 30
_WEEK_MIN = 7 * 24 * 60
_BACKWARDS_FOLLOW_MIN = 240
_BACKWARDS_STALE_MIN = 24 * 60
_TIMELINE_LOOKBACK = timedelta(days=8)
_TIMELINE_MAX_FIRES = 20000
_BAND_MERGE_GAP = timedelta(hours=2)
_RECENT_MAX_RUNS = 5
_MIN_BLOCK_SEC = 60
_LOCAL_STAMP = "%Y-%m-%d %H:%M:%S"
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


def _job_status(node: dict) -> tuple[str, str]:
    if not node["enabled"]:
        return "disabled", "disabled"
    if node.get("last_status") == "error":
        return "red", "error"
    last_run = parse_stored_utc(node.get("last_run"))
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
            "consumes": list(meta.get("consumes", [])),
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
    if meta.get("non_job"):
        return {
            "id": job_id,
            "label": meta["label"],
            "category": "manual",
            "engine": meta["engine"],
            "produces": list(meta.get("produces", [])),
            "consumes": list(meta.get("consumes", [])),
            "enabled": True,
            "last_run": None,
            "last_status": None,
            "avg_duration_sec": None,
            "next_run": None,
            "schedule": None,
            "status": "manual",
            "status_reason": "Manual data entry — not a scheduled job",
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
    consumes = list(meta.get("consumes", []))
    if _DYNAMIC_ETF_RE.match(job_id):
        label = job_label(job_id)
    elif _DYNAMIC_ACCOUNT_SCRAPER_RE.match(job_id):
        account_id = int(job_id.split("_")[2])
        acc = get_account(account_id)
        label = f"Account Price Scraper — {acc['name'] if acc else account_id}"
        artifact = _ACCOUNT_TYPE_ARTIFACT.get(acc["account_type"] if acc else None)
        if artifact:
            consumes = [artifact]
    elif _DYNAMIC_ACCOUNT_TOPUP_RE.match(job_id):
        account_id = int(job_id.split("_")[2])
        acc = get_account(account_id)
        label = f"Account Auto Top-up — {acc['name'] if acc else account_id}"
    elif _DYNAMIC_ACCOUNT_REMINDER_RE.match(job_id):
        label = reminder_job_display_name(int(job_id.split("_")[2]), meta["label"])
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
        "consumes": consumes,
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


def classify_dependency_gap(back_gap_min: float, fwd_gap_min: float, avg_duration_sec: float | None) -> tuple[str, bool]:
    """Shared by the Workflow Monitor conflict check and the Job Scheduling Timeline so both judge a producer→consumer gap identically."""
    if avg_duration_sec is None:
        overlap = "unknown" if back_gap_min < _UNKNOWN_GAP_MIN else "ok"
    elif back_gap_min < avg_duration_sec / 60.0:
        overlap = "violation"
    elif back_gap_min < avg_duration_sec / 60.0 + _OVERLAP_BUFFER_MIN:
        overlap = "tight"
    else:
        overlap = "ok"
    backwards = fwd_gap_min <= _BACKWARDS_FOLLOW_MIN and back_gap_min >= _BACKWARDS_STALE_MIN
    return overlap, backwards


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
        overlap, backwards = classify_dependency_gap(back_gap, fwd_gap, avg)
        if overlap == "unknown":
            conflicts.append({
                "type": "overlap_risk", "severity": "info",
                "job_id": consumer["id"], "related": producer["id"],
                "message": f"{consumer['label']} starts {back_gap} min after {producer['label']} (its source of {edge['via']}); the producer's typical runtime is not yet known, so overlap cannot be ruled out.",
            })
        elif overlap in ("tight", "violation"):
            conflicts.append({
                "type": "overlap_risk", "severity": "warning",
                "job_id": consumer["id"], "related": producer["id"],
                "message": f"{consumer['label']} starts {back_gap} min after {producer['label']} (its source of {edge['via']}), but {producer['label']} typically runs ~{avg / 60.0:.0f} min — it may still be running, so {consumer['label']} could read incomplete data.",
            })
        if backwards:
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


def timeline_window_start(day: date | None) -> datetime:
    """UTC instant of local midnight on *day*, defaulting to Monday of the current local week."""
    if day is None:
        today = now_local().date()
        day = today - timedelta(days=today.weekday())
    return datetime.combine(day, time(0), tzinfo=get_user_tz()).astimezone(timezone.utc)


def _expand_fires(trigger, start: datetime, end: datetime) -> list[datetime]:
    fires, prev, now = [], None, start
    while len(fires) < _TIMELINE_MAX_FIRES:
        nxt = trigger.get_next_fire_time(prev, now)
        if nxt is None or nxt >= end:
            break
        if nxt >= start:
            fires.append(nxt.astimezone(timezone.utc))
        prev, now = nxt, nxt + timedelta(microseconds=1)
    return fires


def _apply_override(fires: list[datetime], hhmm: str, tz) -> list[datetime]:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return sorted(f.astimezone(tz).replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(timezone.utc) for f in fires)


def _single_daily_time(trigger) -> bool:
    try:
        fields = {f.name: str(f) for f in trigger.fields}
    except AttributeError:
        return False
    return fields.get("day", "*") in ("*", "?") and fields.get("hour", "").isdigit() and fields.get("minute", "").isdigit()


def _local(dt: datetime | None) -> str | None:
    return None if dt is None else to_local(dt).strftime(_LOCAL_STAMP)


def _max_fires_per_day(fires: list[datetime]) -> int:
    per_day = defaultdict(int)
    for f in fires:
        per_day[to_local(f).date()] += 1
    return max(per_day.values(), default=0)


def _pack_lanes(blocks: list[dict]) -> int:
    lane_ends: list[datetime] = []
    for b in sorted(blocks, key=lambda b: b["_start"]):
        for i, lane_end in enumerate(lane_ends):
            if lane_end <= b["_start"]:
                b["lane"], lane_ends[i] = i, b["_end"]
                break
        else:
            b["lane"] = len(lane_ends)
            lane_ends.append(b["_end"])
    ordered = sorted(blocks, key=lambda b: b["_start"])
    for i, b in enumerate(ordered):
        for other in ordered[i + 1:]:
            if other["_start"] >= b["_end"]:
                break
            if other["job_id"] != b["job_id"]:
                b["overlap_with"].append(other["job_id"])
                other["overlap_with"].append(b["job_id"])
    return len(lane_ends)


def _merge_bands(job_id: str, fires: list[datetime], duration_sec: float) -> list[dict]:
    bands, dur = [], timedelta(seconds=duration_sec)
    if not fires:
        return bands
    band_start = prev = fires[0]
    count = 1
    for f in fires[1:]:
        if f - prev <= _BAND_MERGE_GAP:
            prev, count = f, count + 1
            continue
        bands.append({"job_id": job_id, "start": _local(band_start), "end": _local(prev + dur), "fires": count})
        band_start = prev = f
        count = 1
    bands.append({"job_id": job_id, "start": _local(band_start), "end": _local(prev + dur), "fires": count})
    return bands


def build_schedule_timeline(start_utc: datetime, days: int, overrides: dict[str, str] | None = None) -> dict:
    """Planned, actual and dependency data for the Job Scheduling Timeline; *overrides* ({job_id: "HH:MM"}) re-times single-time daily jobs for what-if checks."""
    from scheduler_engine import scheduler, get_all_job_last_runs, get_all_job_run_history
    from scheduler_manifest import CONFIG_KEY_TO_JOB
    overrides = overrides or {}
    end_utc = start_utc + timedelta(days=days)
    user_tz = get_user_tz()
    graph = build_workflow_graph()
    live = {j.id: j for j in scheduler.get_jobs()}
    last_runs = get_all_job_last_runs()
    history: dict[str, list[dict]] = defaultdict(list)
    for row in get_all_job_run_history():
        history[row["job_id"]].append(row)
    config_key_for = {job_id: key for key, job_id in CONFIG_KEY_TO_JOB.items()}
    nodes = {n["id"]: n for n in graph["nodes"] if n["id"] in live and n["category"] not in ("external", "manual")}
    labels = {n["id"]: n["label"] for n in graph["nodes"]}

    fires: dict[str, list[datetime]] = {}
    rows: dict[str, str] = {}
    per_day: dict[str, int] = {}
    for job_id in nodes:
        trigger = live[job_id].trigger
        window_fires = _expand_fires(trigger, start_utc, end_utc)
        per_day[job_id] = _max_fires_per_day(window_fires)
        rows[job_id] = "scheduled" if per_day[job_id] <= 1 else "frequent"
        if rows[job_id] == "scheduled":
            window_fires = _expand_fires(trigger, start_utc - _TIMELINE_LOOKBACK, start_utc) + window_fires
            if job_id in overrides and _single_daily_time(trigger):
                window_fires = _apply_override(window_fires, overrides[job_id], getattr(trigger, "timezone", None) or user_tz)
        fires[job_id] = window_fires

    upstream, downstream = defaultdict(set), defaultdict(set)
    dep_vias: dict[tuple[str, str], list[str]] = defaultdict(list)
    for edge in graph["edges"]:
        upstream[edge["to"]].add(edge["from"])
        downstream[edge["from"]].add(edge["to"])
        if rows.get(edge["from"]) == "scheduled" and rows.get(edge["to"]) == "scheduled":
            dep_vias[(edge["from"], edge["to"])].append(edge["via"])

    jobs, recent_max, avg = [], {}, {}
    for job_id, node in nodes.items():
        runs = history.get(job_id, [])
        ok = [r["duration_sec"] for r in runs if r["status"] == "success"][:_RECENT_MAX_RUNS]
        recent_max[job_id] = max(ok) if ok else None
        avg[job_id] = node.get("avg_duration_sec")
        lr = last_runs.get(job_id, {})
        started, finished = parse_stored_utc(lr.get("last_started")), parse_stored_utc(lr.get("last_run"))
        trigger = live[job_id].trigger
        config_key = config_key_for.get(job_id)
        jobs.append({
            "id": job_id,
            "label": node["label"],
            "category": node["category"],
            "row": rows[job_id],
            "max_fires_per_day": per_day[job_id],
            "avg_duration_sec": avg[job_id],
            "recent_max_sec": recent_max[job_id],
            "status": node["status"],
            "status_reason": node["status_reason"],
            "running": started is not None and (finished is None or started > finished),
            "settings_anchor": node.get("settings_anchor"),
            "upstream": sorted(labels.get(j, j) for j in upstream[job_id]),
            "downstream": sorted(labels.get(j, j) for j in downstream[job_id]),
            "history_from": _local(parse_stored_utc(runs[-1]["started_at"])) if len(runs) >= SCHEDULER_RUN_HISTORY_LIMIT else None,
            "reschedule": None if config_key is None else {
                "config_key": config_key,
                "field": "TIME",
                "editable": rows[job_id] == "scheduled" and _single_daily_time(trigger),
            },
        })

    blocks, bands = [], []
    draw_from = start_utc - timedelta(days=1)
    for job_id in nodes:
        dur = max(avg[job_id] or 0, _MIN_BLOCK_SEC)
        if rows[job_id] == "frequent":
            bands.extend(_merge_bands(job_id, fires[job_id], dur))
            continue
        for f in fires[job_id]:
            if f < draw_from:
                continue
            whisker = recent_max[job_id] if recent_max[job_id] and recent_max[job_id] > dur else None
            blocks.append({
                "job_id": job_id,
                "_start": f,
                "_end": f + timedelta(seconds=dur),
                "start": _local(f),
                "end": _local(f + timedelta(seconds=dur)),
                "whisker_end": _local(f + timedelta(seconds=whisker)) if whisker else None,
                "overlap_with": [],
            })
    lanes = _pack_lanes(blocks)
    for b in blocks:
        del b["_start"], b["_end"]
        b["overlap_with"] = sorted(set(b["overlap_with"]))

    dependencies = []
    lookback_min = _TIMELINE_LOOKBACK.total_seconds() / 60.0
    for (producer_id, consumer_id), vias in dep_vias.items():
        p_fires = fires[producer_id]
        p_avg, p_max = avg[producer_id], recent_max[producer_id]
        for c in fires[consumer_id]:
            if c < start_utc:
                continue
            i_prev, i_next = bisect_right(p_fires, c), bisect_left(p_fires, c)
            p_prev = p_fires[i_prev - 1] if i_prev else None
            p_next = p_fires[i_next] if i_next < len(p_fires) else None
            back_gap = (c - p_prev).total_seconds() / 60.0 if p_prev else lookback_min
            fwd_gap = (p_next - c).total_seconds() / 60.0 if p_next else float("inf")
            overlap, backwards = classify_dependency_gap(back_gap, fwd_gap, p_avg)
            dependencies.append({
                "from": producer_id,
                "to": consumer_id,
                "via": sorted(vias),
                "producer_start": _local(p_prev),
                "producer_end": _local(p_prev + timedelta(seconds=p_avg)) if p_prev and p_avg is not None else None,
                "consumer_start": _local(c),
                "gap_min": round(back_gap, 1) if p_prev else None,
                "overlap": overlap,
                "backwards": backwards,
                "recent_overrun": bool(p_prev and p_max and p_prev + timedelta(seconds=p_max) > c),
                "margin_min": _OVERLAP_BUFFER_MIN,
            })

    runs_out = []
    for job_id in nodes:
        for r in history.get(job_id, []):
            started, finished = parse_stored_utc(r["started_at"]), parse_stored_utc(r["finished_at"])
            if started is None or finished is None or finished < draw_from or started >= end_utc:
                continue
            runs_out.append({
                "job_id": job_id, "start": _local(started), "end": _local(finished),
                "duration_sec": round(r["duration_sec"], 1), "status": r["status"], "trigger": r["trigger_source"],
            })

    jobs.sort(key=lambda j: (j["row"] != "scheduled", j["category"], j["label"]))
    return {
        "timezone": str(user_tz),
        "window": {"start": _local(start_utc), "end": _local(end_utc), "now": _local(datetime.now(timezone.utc))},
        "lanes": lanes,
        "jobs": jobs,
        "blocks": blocks,
        "bands": bands,
        "runs": runs_out,
        "dependencies": dependencies,
    }
