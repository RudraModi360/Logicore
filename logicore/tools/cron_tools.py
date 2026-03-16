from __future__ import annotations

from datetime import datetime
import json
from typing import Optional

from pydantic import BaseModel, Field

from logicore.cron.service import get_global_cron_service
from logicore.cron.types import CronSchedule
from .base import BaseTool, ToolResult


_CRON_SERVICE = get_global_cron_service()


def _format_ms(ms: Optional[int]) -> str:
    if ms is None:
        return "never"
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


class AddCronJobParams(BaseModel):
    name: str = Field(..., description="Short name for the scheduled job")
    message: str = Field(..., description="Message to deliver when the job fires")
    cron_expression: str = Field(
        ...,
        description="Cron expression in 5-field format: minute hour day month weekday (e.g. '0 9 * * *')",
    )


class AddCronJobTool(BaseTool):
    name = "add_cron_job"
    description = "Add a new cron scheduled task with persistent storage and restart recovery."
    args_schema = AddCronJobParams

    def run(
        self,
        name: str,
        message: str,
        cron_expression: str,
    ) -> ToolResult:
        try:
            service = _CRON_SERVICE
            schedule = CronSchedule(kind="cron", expr=cron_expression)
            job = service.add_job(
                name=name,
                schedule=schedule,
                message=message,
                to="agent",
            )
            return ToolResult(
                success=True,
                content=(
                    f"Job '{job.name}' scheduled successfully. "
                    f"ID: {job.id}. Next run: {_format_ms(job.state.next_run_at_ms)}"
                ),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"{exc}. Use 5-field cron format: minute hour day month weekday.",
            )


class ListCronJobsParams(BaseModel):
    include_disabled: bool = Field(False, description="Include disabled jobs in the result")


class ListCronJobsTool(BaseTool):
    name = "list_cron_jobs"
    description = "List all scheduled cron tasks currently stored by the system."
    args_schema = ListCronJobsParams

    def run(self, include_disabled: bool = False) -> ToolResult:
        try:
            service = _CRON_SERVICE
            jobs = service.list_jobs(include_disabled=include_disabled)
            if not jobs:
                return ToolResult(success=True, content="No scheduled jobs found.")

            lines = []
            for job in jobs:
                lines.append(
                    "- "
                    f"[{job.id}] {job.name} | cron='{job.schedule.expr}' | "
                    f"next={_format_ms(job.state.next_run_at_ms)} | "
                    f"runs={job.state.run_count}"
                )

            return ToolResult(success=True, content="\n".join(lines))
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


class RemoveCronJobParams(BaseModel):
    job_id: str = Field(..., description="The job ID to remove")


class RemoveCronJobTool(BaseTool):
    name = "remove_cron_job"
    description = "Delete a scheduled cron task by ID."
    args_schema = RemoveCronJobParams

    def run(self, job_id: str) -> ToolResult:
        try:
            service = _CRON_SERVICE
            removed = service.remove_job(job_id)
            if removed:
                return ToolResult(success=True, content=f"Job {job_id} removed successfully.")
            return ToolResult(success=False, error=f"Job {job_id} not found.")
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


class GetCronsParams(BaseModel):
    include_disabled: bool = Field(False, description="Include disabled jobs in the JSON payload")


class GetCronsTool(BaseTool):
    name = "get_crons"
    description = "Get structured cron metadata and last five executions for each active cron job."
    args_schema = GetCronsParams

    def run(self, include_disabled: bool = False) -> ToolResult:
        try:
            payload = _CRON_SERVICE.get_crons(include_disabled=include_disabled)
            return ToolResult(success=True, content=json.dumps(payload, indent=2))
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
