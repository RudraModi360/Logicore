from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .types import CronJob, CronPayload, CronSchedule, CronStore


OnCronJobCallback = Callable[[CronJob], Awaitable[object] | object]


class CronService:
    def __init__(
        self,
        store_path: Path,
        on_job: Optional[OnCronJobCallback] = None,
        poll_interval_seconds: float = 1.0,
    ):
        self.store_path = Path(store_path)
        self._store = CronStore()
        self._on_job = on_job
        self._poll_interval_seconds = max(0.25, float(poll_interval_seconds))

        self._lock = threading.RLock()
        self._started = False
        self._start_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def set_on_job(self, callback: Optional[OnCronJobCallback]) -> None:
        self._on_job = callback

    def start_background(self) -> None:
        with self._start_lock:
            if self._started:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._thread_main, daemon=True, name="logicore-cron")
            self._thread.start()
            self._started = True

    def stop(self, timeout_seconds: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout_seconds)
        self._started = False

    async def start(self) -> None:
        self._load_store()
        await self._check_missed_jobs()
        while not self._stop_event.is_set():
            await self._tick()
            await asyncio.sleep(self._poll_interval_seconds)

    def list_jobs(self, include_disabled: bool = False) -> List[CronJob]:
        with self._lock:
            jobs = list(self._store.jobs)
        if include_disabled:
            return jobs
        return [job for job in jobs if job.enabled]

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        to: str = "agent",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CronJob:
        now_ms = _now_ms()
        next_run_ms = self._compute_next_run(schedule, from_ms=now_ms)
        if next_run_ms is None:
            raise ValueError("Invalid schedule. Could not compute next run time.")

        job = CronJob(
            id=str(uuid.uuid4()),
            name=name,
            schedule=schedule,
            payload=CronPayload(message=message, to=to, metadata=metadata or {}),
            enabled=True,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )
        job.state.next_run_at_ms = next_run_ms

        with self._lock:
            self._store.jobs.append(job)
            self._save_store_locked()
        return job

    def remove_job(self, job_id: str) -> bool:
        with self._lock:
            initial_count = len(self._store.jobs)
            self._store.jobs = [job for job in self._store.jobs if job.id != job_id]
            changed = len(self._store.jobs) != initial_count
            if changed:
                self._save_store_locked()
            return changed

    def get_crons(self, include_disabled: bool = False) -> Dict[str, Any]:
        jobs = self.list_jobs(include_disabled=include_disabled)
        payload = {
            "generated_at_ms": _now_ms(),
            "active_count": sum(1 for job in jobs if job.enabled),
            "total_count": len(jobs),
            "jobs": [self._job_to_metadata(job) for job in jobs if include_disabled or job.enabled],
        }
        return payload

    def _job_to_metadata(self, job: CronJob) -> Dict[str, Any]:
        return {
            "id": job.id,
            "name": job.name,
            "enabled": job.enabled,
            "schedule": {
                "kind": job.schedule.kind,
                "expr": job.schedule.expr,
            },
            "payload": {
                "to": job.payload.to,
                "message": job.payload.message,
                "metadata": job.payload.metadata,
            },
            "state": {
                "next_run_at_ms": job.state.next_run_at_ms,
                "last_run_at_ms": job.state.last_run_at_ms,
                "run_count": job.state.run_count,
                "last_error": job.state.last_error,
                "recent_executions": list(job.state.recent_executions[-5:]),
            },
            "created_at_ms": job.created_at_ms,
            "updated_at_ms": job.updated_at_ms,
        }

    def _thread_main(self) -> None:
        try:
            asyncio.run(self.start())
        except Exception:
            self._started = False

    async def _tick(self) -> None:
        now_ms = _now_ms()
        with self._lock:
            due_jobs = [
                job for job in self._store.jobs
                if job.enabled and job.state.next_run_at_ms is not None and now_ms >= job.state.next_run_at_ms
            ]

        for job in due_jobs:
            await self._execute_job(job, is_missed=False)

    async def _check_missed_jobs(self) -> None:
        now_ms = _now_ms()
        with self._lock:
            missed_jobs = [
                job for job in self._store.jobs
                if job.enabled and job.state.next_run_at_ms is not None and now_ms > job.state.next_run_at_ms
            ]

        for job in missed_jobs:
            await self._execute_job(job, is_missed=True)

    async def _execute_job(self, job: CronJob, is_missed: bool) -> None:
        now_ms = _now_ms()
        scheduled_at_ms = job.state.next_run_at_ms
        status = "success"
        error_message: Optional[str] = None

        try:
            if self._on_job:
                result = self._on_job(job)
                if asyncio.iscoroutine(result):
                    await result

            job.state.last_error = None
        except Exception as exc:
            status = "error"
            error_message = str(exc)
            job.state.last_error = error_message
        finally:
            executed_at_ms = _now_ms()
            entry = {
                "scheduled_at_ms": scheduled_at_ms,
                "executed_at_ms": executed_at_ms,
                "status": status,
                "error": error_message,
                "is_missed": bool(is_missed),
                "target": job.payload.to,
            }
            recent = list(job.state.recent_executions)
            recent.append(entry)
            job.state.recent_executions = recent[-5:]

            job.state.last_run_at_ms = now_ms
            job.state.run_count += 1
            job.updated_at_ms = executed_at_ms
            job.state.next_run_at_ms = self._compute_next_run(job.schedule, from_ms=executed_at_ms)
            with self._lock:
                self._save_store_locked()

    def _load_store(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.store_path.exists():
            with self._lock:
                self._store = CronStore()
                self._save_store_locked()
            return

        try:
            raw = self.store_path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            store = CronStore.from_dict(data or {})
        except Exception:
            store = CronStore()

        normalized = False
        for job in store.jobs:
            if job.payload.to != "agent":
                job.payload.to = "agent"
                normalized = True

        with self._lock:
            self._store = store
            if normalized:
                self._save_store_locked()

    def _save_store_locked(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.store_path.with_suffix(self.store_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(self._store.to_dict(), indent=2), encoding="utf-8")
        tmp_path.replace(self.store_path)

    def _compute_next_run(self, schedule: CronSchedule, from_ms: Optional[int] = None) -> Optional[int]:
        current_ms = _now_ms() if from_ms is None else int(from_ms)

        if schedule.kind == "at":
            target_ms = _parse_at_expr_to_ms(schedule.expr)
            if target_ms is None:
                return None
            return target_ms if target_ms > current_ms else None

        if schedule.kind == "every":
            interval_ms = _parse_interval_expr_to_ms(schedule.expr)
            if interval_ms is None:
                return None
            return current_ms + interval_ms

        if schedule.kind == "cron":
            return _next_cron_time_ms(schedule.expr, current_ms)

        return None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _parse_at_expr_to_ms(expr: str) -> Optional[int]:
    expr = str(expr).strip()
    if expr.isdigit():
        value = int(expr)
        return value if value > 10_000_000_000 else value * 1000

    try:
        dt = datetime.fromisoformat(expr.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def _parse_interval_expr_to_ms(expr: str) -> Optional[int]:
    expr = str(expr).strip().lower()
    match = re.fullmatch(r"(\d+)\s*([smhd])", expr)
    if not match:
        if expr.isdigit():
            return int(expr) * 1000
        return None

    value = int(match.group(1))
    unit = match.group(2)
    multiplier = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
    }[unit]
    return value * multiplier * 1000


def _parse_cron_field(field: str, minimum: int, maximum: int) -> Optional[set[int]]:
    field = field.strip()
    if field == "*":
        return set(range(minimum, maximum + 1))

    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            base, step_str = part.split("/", 1)
            if not step_str.isdigit() or int(step_str) <= 0:
                return None
            step = int(step_str)
        else:
            base = part

        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_str, end_str = base.split("-", 1)
            if not (start_str.isdigit() and end_str.isdigit()):
                return None
            start, end = int(start_str), int(end_str)
        else:
            if not base.isdigit():
                return None
            start = end = int(base)

        if start < minimum or end > maximum or start > end:
            return None

        values.update(range(start, end + 1, step))

    return values


def _next_cron_time_ms(expr: str, from_ms: int) -> Optional[int]:
    parts = [p for p in expr.split() if p]
    if len(parts) != 5:
        return None

    minute_set = _parse_cron_field(parts[0], 0, 59)
    hour_set = _parse_cron_field(parts[1], 0, 23)
    day_set = _parse_cron_field(parts[2], 1, 31)
    month_set = _parse_cron_field(parts[3], 1, 12)
    weekday_set = _parse_cron_field(parts[4], 0, 7)
    if not all([minute_set, hour_set, day_set, month_set, weekday_set]):
        return None

    dom_any = parts[2].strip() == "*"
    dow_any = parts[4].strip() == "*"

    if 7 in weekday_set:
        weekday_set.add(0)

    cursor = datetime.fromtimestamp(from_ms / 1000)
    cursor = cursor.replace(second=0, microsecond=0)
    cursor = datetime.fromtimestamp(cursor.timestamp() + 60)

    for _ in range(0, 60 * 24 * 366 * 3):
        cron_weekday = (cursor.weekday() + 1) % 7
        if dom_any and dow_any:
            day_match = True
        elif dom_any:
            day_match = cron_weekday in weekday_set
        elif dow_any:
            day_match = cursor.day in day_set
        else:
            day_match = (cursor.day in day_set) or (cron_weekday in weekday_set)

        if (
            cursor.minute in minute_set
            and cursor.hour in hour_set
            and day_match
            and cursor.month in month_set
        ):
            return int(cursor.timestamp() * 1000)
        cursor = datetime.fromtimestamp(cursor.timestamp() + 60)

    return None


_global_cron_service: Optional[CronService] = None
_global_cron_lock = threading.Lock()


def get_global_cron_service(on_job: Optional[OnCronJobCallback] = None) -> CronService:
    global _global_cron_service

    with _global_cron_lock:
        if _global_cron_service is None:
            root = Path(__file__).resolve().parent.parent
            store_path = root / "user_data" / "cron" / "cron_jobs.json"
            _global_cron_service = CronService(store_path=store_path, on_job=on_job)
        elif on_job is not None:
            _global_cron_service.set_on_job(on_job)

        _global_cron_service.start_background()
        return _global_cron_service


def get_crons(include_disabled: bool = False) -> Dict[str, Any]:
    service = get_global_cron_service()
    return service.get_crons(include_disabled=include_disabled)
