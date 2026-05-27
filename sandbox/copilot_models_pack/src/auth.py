import os
import shutil
import subprocess
import time
import webbrowser
import ctypes
from typing import Any, Callable, Dict, Optional

import requests


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


class CopilotAuthError(RuntimeError):
    pass


class DeviceFlowAuthClient:
    """OAuth device-flow helper for sandbox validation.

    This is intentionally standalone so we can test auth behavior before
    integrating with logicore provider routing.
    """

    DEVICE_CODE_URL = "https://github.com/login/device/code"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    USER_URL = "https://api.github.com/user"
    # Public OAuth app client id used by GitHub CLI/device-flow style login.
    # Keeping this as default removes the need for users to provide client IDs.
    DEFAULT_CLIENT_ID = "Iv1.b507a08c87ecfe98"

    def __init__(self, client_id: Optional[str] = None, scope: str = "read:user"):
        self.client_id = client_id or _env("GITHUB_OAUTH_CLIENT_ID") or self.DEFAULT_CLIENT_ID
        self.scope = scope

    @staticmethod
    def _notify(status_callback: Optional[Callable[[str], None]], message: str) -> None:
        if status_callback:
            status_callback(message)
            return
        print(f"[copilot_login] {message}")

    @staticmethod
    def _show_popup(title: str, message: str) -> None:
        """Best-effort desktop notification popup (Windows only in this sandbox)."""
        if os.name != "nt":
            return
        try:
            # MB_ICONINFORMATION (0x40) | MB_OK (0x0)
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)
        except Exception:
            return

    @staticmethod
    def _gh_exists() -> bool:
        return shutil.which("gh") is not None

    @staticmethod
    def _gh_token() -> Optional[str]:
        if not DeviceFlowAuthClient._gh_exists():
            return None
        proc = subprocess.run(
            ["gh", "auth", "token"],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return None
        token = (proc.stdout or "").strip()
        return token or None

    def login_with_gh_cli_browser(
        self,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        if not self._gh_exists():
            raise CopilotAuthError("GitHub CLI (gh) is not installed or not on PATH.")

        token = self._gh_token()
        if token:
            validation = self.validate_token(token)
            if validation.get("valid"):
                self._notify(status_callback, "Using existing GitHub CLI login session.")
                return {
                    "access_token": token,
                    "source": "gh_cli",
                    "validation": validation,
                }

        self._notify(status_callback, "Opening browser via GitHub CLI login...")
        # Keep stdio attached so the user sees prompts and can finish login.
        proc = subprocess.run(
            ["gh", "auth", "login", "-h", "github.com", "-p", "https", "-w"],
            check=False,
        )
        if proc.returncode != 0:
            raise CopilotAuthError("GitHub CLI login failed. Please retry in your terminal.")

        token = self._gh_token()
        if not token:
            raise CopilotAuthError("GitHub CLI login succeeded but no token could be retrieved.")

        validation = self.validate_token(token)
        if not validation.get("valid"):
            raise CopilotAuthError(f"GitHub CLI token validation failed: {validation}")

        return {
            "access_token": token,
            "source": "gh_cli",
            "validation": validation,
        }

    @staticmethod
    def _post_form(url: str, data: Dict[str, Any]) -> Dict[str, Any]:
        response = requests.post(
            url,
            data=data,
            headers={"Accept": "application/json", "User-Agent": "logicore-copilot-sandbox"},
            timeout=30,
        )
        payload = response.json() if response.content else {}
        if response.status_code >= 400:
            raise CopilotAuthError(f"OAuth request failed ({response.status_code}): {payload}")
        return payload

    @staticmethod
    def validate_token(token: str) -> Dict[str, Any]:
        response = requests.get(
            DeviceFlowAuthClient.USER_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "logicore-copilot-sandbox",
            },
            timeout=30,
        )
        if response.status_code == 401:
            return {"valid": False, "reason": "unauthorized"}
        if response.status_code >= 400:
            return {
                "valid": False,
                "reason": "request_failed",
                "status_code": response.status_code,
                "payload": response.text,
            }

        payload = response.json() if response.content else {}
        return {
            "valid": True,
            "login": payload.get("login"),
            "id": payload.get("id"),
            "name": payload.get("name"),
        }

    def login_with_device_flow(
        self,
        status_callback: Optional[Callable[[str], None]] = None,
        open_browser: bool = True,
        poll_timeout_seconds: int = 900,
        show_popup: bool = True,
    ) -> Dict[str, Any]:
        start = time.time()

        if not self.client_id:
            raise CopilotAuthError(
                "OAuth client id is required for raw device flow. "
                "Set GITHUB_OAUTH_CLIENT_ID or use GitHub CLI browser login."
            )

        device_payload = self._post_form(
            self.DEVICE_CODE_URL,
            {
                "client_id": self.client_id,
                "scope": self.scope,
            },
        )

        device_code = device_payload.get("device_code")
        user_code = device_payload.get("user_code")
        verification_uri = device_payload.get("verification_uri")
        verification_uri_complete = device_payload.get("verification_uri_complete")
        interval = int(device_payload.get("interval") or 5)

        if not device_code or not user_code or not verification_uri:
            raise CopilotAuthError(f"Unexpected device-flow payload: {device_payload}")

        self._notify(status_callback, f"User code: {user_code}")
        self._notify(status_callback, f"Verification URI: {verification_uri}")
        if verification_uri_complete:
            self._notify(status_callback, f"Verification URL: {verification_uri_complete}")

        popup_enabled = show_popup and os.environ.get("COPILOT_LOGIN_POPUP", "true").lower() in {"1", "true", "yes"}
        if popup_enabled:
            popup_url = verification_uri_complete or verification_uri
            popup_msg = (
                "GitHub verification is required.\n\n"
                f"Code: {user_code}\n"
                f"Open: {popup_url}\n\n"
                "Complete sign in in your browser, then return to terminal."
            )
            self._show_popup("Copilot Login Verification", popup_msg)

        if open_browser and verification_uri_complete:
            try:
                webbrowser.open(verification_uri_complete)
                self._notify(status_callback, "Opened browser for GitHub authorization.")
            except Exception:
                self._notify(status_callback, "Unable to open browser automatically. Open verification URL manually.")

        while True:
            if time.time() - start > poll_timeout_seconds:
                raise CopilotAuthError("Device-flow login timed out.")

            time.sleep(interval)
            token_payload = self._post_form(
                self.TOKEN_URL,
                {
                    "client_id": self.client_id,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )

            access_token = token_payload.get("access_token")
            if access_token:
                validation = self.validate_token(access_token)
                return {
                    "access_token": access_token,
                    "token_type": token_payload.get("token_type", "bearer"),
                    "scope": token_payload.get("scope", self.scope),
                    "verification_uri": verification_uri,
                    "verification_uri_complete": verification_uri_complete,
                    "user_code": user_code,
                    "validation": validation,
                }

            error = token_payload.get("error")
            if error == "authorization_pending":
                elapsed = int(time.time() - start)
                self._notify(status_callback, f"authorization_pending ({elapsed}s elapsed)")
                continue
            if error == "slow_down":
                interval = min(interval + 5, 30)
                self._notify(status_callback, "slow_down")
                continue
            if error:
                raise CopilotAuthError(f"Token exchange failed: {token_payload}")

    def login_with_browser_auth(
        self,
        status_callback: Optional[Callable[[str], None]] = None,
        open_browser: bool = True,
        poll_timeout_seconds: int = 900,
        prefer_gh_cli: bool = True,
        show_popup: bool = True,
    ) -> Dict[str, Any]:
        if prefer_gh_cli:
            try:
                return self.login_with_gh_cli_browser(status_callback=status_callback)
            except Exception as exc:
                self._notify(status_callback, f"GitHub CLI login unavailable, falling back to device flow: {exc}")

        return self.login_with_device_flow(
            status_callback=status_callback,
            open_browser=open_browser,
            poll_timeout_seconds=poll_timeout_seconds,
            show_popup=show_popup,
        )


def copilot_login(
    status_callback: Optional[Callable[[str], None]] = None,
    open_browser: bool = True,
    prefer_gh_cli: bool = True,
    client_id: Optional[str] = None,
    show_popup: bool = True,
) -> Dict[str, Any]:
    """One-call browser auth for Copilot-style usage.

    This function is intentionally simple for end users:
    - Try GitHub CLI browser login first
    - Fallback to raw OAuth device flow with a built-in default client id
    """
    auth_client = DeviceFlowAuthClient(client_id=client_id)
    return auth_client.login_with_browser_auth(
        status_callback=status_callback,
        open_browser=open_browser,
        prefer_gh_cli=prefer_gh_cli,
        show_popup=show_popup,
    )
