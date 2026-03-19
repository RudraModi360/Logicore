"""
Windows Toast Notification System for Cron Jobs
Sends native Windows notifications when cron jobs execute
"""

import json
import os
import html
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Protocol


NotificationChannelName = str


@dataclass
class NotificationRequest:
    title: str
    message: str
    duration: str = "short"
    app_id: str = "Logicore"


@dataclass
class NotificationDispatchResult:
    delivered: bool
    channel_used: Optional[NotificationChannelName] = None
    attempted_channels: List[NotificationChannelName] = field(default_factory=list)
    error: Optional[str] = None


class NotificationChannel(Protocol):
    name: NotificationChannelName

    def send(self, request: NotificationRequest) -> bool:
        ...


class ToastNotificationChannel:
    name = "toast"

    def send(self, request: NotificationRequest) -> bool:
        return send_nanobot_style_toast(
            title=request.title,
            message=request.message,
            app_id=request.app_id,
            duration=request.duration,
        )


class ConsoleNotificationChannel:
    name = "console"

    def send(self, request: NotificationRequest) -> bool:
        print(f"[Cron] {request.title}: {request.message}")
        return True


class CronNotificationManager:
    def __init__(self) -> None:
        self._channels: Dict[NotificationChannelName, NotificationChannel] = {}
        self.register(ToastNotificationChannel())
        self.register(ConsoleNotificationChannel())

    def register(self, channel: NotificationChannel) -> None:
        self._channels[channel.name] = channel

    def dispatch(
        self,
        title: str,
        message: str,
        channel_preference: str = "auto",
        duration: str = "short",
        app_id: str = "Logicore",
    ) -> NotificationDispatchResult:
        request = NotificationRequest(title=title, message=message, duration=duration, app_id=app_id)

        selected_channel = (channel_preference or "auto").strip().lower()
        if selected_channel in {"popup", "console"}:
            selected_channel = "toast"

        if selected_channel == "auto":
            order: List[NotificationChannelName] = ["toast"]
        else:
            order = [selected_channel]

        attempted: List[NotificationChannelName] = []
        for channel_name in order:
            channel = self._channels.get(channel_name)
            if channel is None:
                continue

            attempted.append(channel_name)
            if channel.send(request):
                return NotificationDispatchResult(
                    delivered=True,
                    channel_used=channel_name,
                    attempted_channels=attempted,
                )

        return NotificationDispatchResult(
            delivered=False,
            attempted_channels=attempted,
            error="No notification channel could deliver the message.",
        )


_global_notification_manager: Optional[CronNotificationManager] = None


def get_cron_notification_manager() -> CronNotificationManager:
    global _global_notification_manager
    if _global_notification_manager is None:
        _global_notification_manager = CronNotificationManager()
    return _global_notification_manager


def send_cron_notification(
    title: str,
    message: str,
    channel_preference: str = "auto",
    duration: str = "short",
    app_id: str = "Logicore",
) -> NotificationDispatchResult:
    manager = get_cron_notification_manager()
    return manager.dispatch(
        title=title,
        message=message,
        channel_preference=channel_preference,
        duration=duration,
        app_id=app_id,
    )


def _resolve_logicore_logo_uri() -> str:
    root = Path(__file__).resolve().parent.parent
    candidates = [
        root / "artifacts" / "logicore_logo.png",
        root / "artifacts" / "image-removebg-preview (1).png",
        root / "artifacts" / "logicore_logo.svg",
    ]
    for path in candidates:
        if path.exists():
            return path.as_uri()
    return ""


def _run_powershell_toast_script(ps_script: str) -> bool:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            check=True,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            text=True,
            timeout=8,
        )
        return result.returncode == 0
    except Exception:
        return False


def _send_powershell_toast_generic(
    title: str,
    message: str,
    app_id: str,
    duration: str,
    logo_uri: str,
) -> bool:
    xml_title = html.escape(title, quote=False)
    xml_message = html.escape(message, quote=False)
    toast_duration = "long" if duration == "long" else "short"
    logo_line = (
        f"<image placement='appLogoOverride' hint-crop='circle' src='{logo_uri}'/>"
        if logo_uri
        else ""
    )

    xml_payload = f"""
<toast duration='{toast_duration}' launch='logicore-cron'>
  <visual>
    <binding template='ToastGeneric'>
      {logo_line}
      <text>{xml_title}</text>
      <text>{xml_message}</text>
      <text placement='attribution'>Logicore</text>
    </binding>
  </visual>
</toast>
""".strip()

    app_id_escaped = app_id.replace('"', '`"')
    use_explicit_notifier = bool(app_id.strip())
    notifier_expr = (
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(\"{app_id_escaped}\")"
        if use_explicit_notifier
        else "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier()"
    )

    ps_script = f"""
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

    $xmlPayload = @"
{xml_payload}
"@

    $toastXml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $toastXml.LoadXml($xmlPayload)
    $toast = [Windows.UI.Notifications.ToastNotification]::new($toastXml)
    $notifier = {notifier_expr}
    $notifier.Show($toast)
    """
    return _run_powershell_toast_script(ps_script)


def _send_powershell_toast_basic(title: str, message: str, app_id: str) -> bool:
    title_escaped = title.replace('"', '`"')
    message_escaped = message.replace('"', '`"')
    app_id_escaped = app_id.replace('"', '`"')
    use_explicit_notifier = bool(app_id.strip())
    notifier_expr = (
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(\"{app_id_escaped}\")"
        if use_explicit_notifier
        else "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier()"
    )

    ps_script = f"""
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $toastXml = [Windows.Data.Xml.Dom.XmlDocument]::new()
    $toastXml.LoadXml($template.GetXml())

    $toastTextElements = $toastXml.GetElementsByTagName("text")
    $toastTextElements.Item(0).AppendChild($toastXml.CreateTextNode("{title_escaped}")) | Out-Null
    $toastTextElements.Item(1).AppendChild($toastXml.CreateTextNode("{message_escaped}")) | Out-Null

    $toast = [Windows.UI.Notifications.ToastNotification]::new($toastXml)
    $notifier = {notifier_expr}
    $notifier.Show($toast)
    """
    return _run_powershell_toast_script(ps_script)


def send_nanobot_style_toast(
    title: str,
    message: str,
    app_id: str = "Logicore",
    duration: str = "short",
) -> bool:
    """Send a strict native Windows toast in a branded ToastGeneric style."""
    if os.name != "nt":
        return False

    logo_uri = _resolve_logicore_logo_uri()

    notifier_candidates: List[str] = [app_id]
    if app_id.lower() != "logicore":
        notifier_candidates.append("Logicore")
    notifier_candidates.append("")

    for candidate in notifier_candidates:
        if _send_powershell_toast_generic(
            title=title,
            message=message,
            app_id=candidate,
            duration=duration,
            logo_uri=logo_uri,
        ):
            return True

    for candidate in notifier_candidates:
        if _send_powershell_toast_basic(title=title, message=message, app_id=candidate):
            return True

    return False


class CronNotifications:
    """Backward-compatible helper around the notification manager."""
    
    @staticmethod
    def send_toast(
        title: str,
        message: str,
        duration: str = "short",
        app_id: str = "CronJobNotifier"
    ) -> bool:
        """
        Send a Windows toast notification
        
        Args:
            title: Notification title
            message: Notification message
            duration: "short" (3 secs) or "long" (7 secs)
            app_id: Application ID
        
        Returns:
            True if successful, False otherwise
        """
        result = send_cron_notification(
            title=title,
            message=message,
            channel_preference="toast",
            duration=duration,
            app_id=app_id,
        )
        return result.delivered


class CronExecutionLog:
    """Track and log cron job executions for validation"""
    
    def __init__(self, log_dir: str = ".cron_logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.execution_log = self.log_dir / "executions.jsonl"
        self.summary_log = self.log_dir / "summary.json"
    
    def log_execution(
        self,
        job_id: str,
        job_name: str,
        scheduled_time: datetime,
        execution_time: datetime,
        status: str = "success",
        error: Optional[str] = None,
        message: Optional[str] = None
    ) -> None:
        """Log a job execution"""
        try:
            entry = {
                "timestamp": execution_time.isoformat(),
                "scheduled_time": scheduled_time.isoformat(),
                "job_id": job_id,
                "job_name": job_name,
                "status": status,
                "error": error,
                "message": message,
                "delay_seconds": (execution_time - scheduled_time).total_seconds()
            }
            
            # Append to execution log
            with open(self.execution_log, 'a') as f:
                f.write(json.dumps(entry) + '\n')
            
            # Update summary
            self._update_summary(job_id, job_name, status)
            
        except Exception as e:
            print(f"[ERROR] Failed to log execution: {e}")
    
    def _update_summary(self, job_id: str, job_name: str, status: str) -> None:
        """Update execution summary"""
        try:
            summary = {}
            if self.summary_log.exists():
                with open(self.summary_log, 'r') as f:
                    summary = json.load(f)
            
            if job_id not in summary:
                summary[job_id] = {
                    "name": job_name,
                    "total_runs": 0,
                    "successful": 0,
                    "failed": 0,
                    "last_run": None,
                    "last_status": None
                }
            
            summary[job_id]["total_runs"] += 1
            if status == "success":
                summary[job_id]["successful"] += 1
            else:
                summary[job_id]["failed"] += 1
            summary[job_id]["last_run"] = datetime.now().isoformat()
            summary[job_id]["last_status"] = status
            
            with open(self.summary_log, 'w') as f:
                json.dump(summary, f, indent=2)
        
        except Exception as e:
            print(f"[ERROR] Failed to update summary: {e}")
    
    def get_executions(self, job_id: Optional[str] = None, limit: int = 10) -> list:
        """Get recent executions"""
        try:
            executions = []
            if self.execution_log.exists():
                with open(self.execution_log, 'r') as f:
                    for line in f:
                        entry = json.loads(line)
                        if job_id is None or entry['job_id'] == job_id:
                            executions.append(entry)
            
            return executions[-limit:]
        except Exception as e:
            print(f"[ERROR] Failed to read executions: {e}")
            return []
    
    def get_summary(self) -> dict:
        """Get execution summary"""
        try:
            if self.summary_log.exists():
                with open(self.summary_log, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            print(f"[ERROR] Failed to read summary: {e}")
            return {}
    
    def print_summary(self) -> None:
        """Print execution summary in readable format"""
        summary = self.get_summary()
        
        if not summary:
            print("[INFO] No executions logged yet")
            return
        
        print("\n" + "="*80)
        print("CRON JOB EXECUTION SUMMARY")
        print("="*80 + "\n")
        
        for job_id, stats in summary.items():
            print(f"Job: {stats['name']} (ID: {job_id})")
            print(f"  Total Runs: {stats['total_runs']}")
            print(f"  Successful: {stats['successful']} ({stats['successful']/max(stats['total_runs'],1)*100:.1f}%)")
            print(f"  Failed: {stats['failed']}")
            print(f"  Last Run: {stats['last_run']}")
            print(f"  Status: {stats['last_status']}\n")
        
        print("="*80)
