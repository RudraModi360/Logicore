from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


ScheduleKind = Literal["at", "every", "cron"]
DeliveryTarget = Literal["notification", "agent"]


@dataclass
class CronSchedule:
    kind: ScheduleKind
    expr: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CronSchedule":
        return cls(kind=data["kind"], expr=str(data["expr"]))

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "expr": self.expr}


@dataclass
class CronPayload:
    message: str
    to: DeliveryTarget = "notification"
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CronPayload":
        return cls(
            message=str(data.get("message", "")),
            to=data.get("to", "notification"),
            metadata=dict(data.get("metadata", {})),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message": self.message,
            "to": self.to,
            "metadata": self.metadata,
        }


@dataclass
class CronJobState:
    next_run_at_ms: Optional[int] = None
    last_run_at_ms: Optional[int] = None
    run_count: int = 0
    last_error: Optional[str] = None
    recent_executions: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CronJobState":
        recent_raw = data.get("recent_executions", [])
        if isinstance(recent_raw, list):
            recent_executions = [item for item in recent_raw if isinstance(item, dict)]
        else:
            recent_executions = []
        return cls(
            next_run_at_ms=data.get("next_run_at_ms"),
            last_run_at_ms=data.get("last_run_at_ms"),
            run_count=int(data.get("run_count", 0)),
            last_error=data.get("last_error"),
            recent_executions=recent_executions,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "next_run_at_ms": self.next_run_at_ms,
            "last_run_at_ms": self.last_run_at_ms,
            "run_count": self.run_count,
            "last_error": self.last_error,
            "recent_executions": self.recent_executions,
        }


@dataclass
class CronJob:
    id: str
    name: str
    schedule: CronSchedule
    payload: CronPayload
    enabled: bool = True
    created_at_ms: int = 0
    updated_at_ms: int = 0
    state: CronJobState = field(default_factory=CronJobState)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CronJob":
        return cls(
            id=str(data["id"]),
            name=str(data.get("name", "Unnamed Job")),
            schedule=CronSchedule.from_dict(data["schedule"]),
            payload=CronPayload.from_dict(data["payload"]),
            enabled=bool(data.get("enabled", True)),
            created_at_ms=int(data.get("created_at_ms", 0)),
            updated_at_ms=int(data.get("updated_at_ms", 0)),
            state=CronJobState.from_dict(data.get("state", {})),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "schedule": self.schedule.to_dict(),
            "payload": self.payload.to_dict(),
            "enabled": self.enabled,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "state": self.state.to_dict(),
        }


@dataclass
class CronStore:
    version: int = 1
    jobs: List[CronJob] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CronStore":
        jobs_data = data.get("jobs", [])
        jobs = [CronJob.from_dict(item) for item in jobs_data]
        return cls(version=int(data.get("version", 1)), jobs=jobs)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "jobs": [job.to_dict() for job in self.jobs],
        }
