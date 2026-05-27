import asyncio
import json
import os
from typing import Any, Callable, Dict, List, Optional

import requests

from .auth import DeviceFlowAuthClient, copilot_login as auth_copilot_login

try:
    import keyring
except ImportError:  # optional in sandbox, but recommended
    keyring = None


class CopilotProviderSandbox:
    """Sandbox-only provider for validating auth and model calls before core integration."""

    provider_name = "copilot"
    KEYRING_SERVICE = "logicore-copilot-sandbox"
    DEFAULT_BASE_URL = "https://models.github.ai/inference"
    DEFAULT_MODELS = ["gpt-4.1", "gpt-4o", "o3-mini"]

    def __init__(
        self,
        model_name: str = "gpt-4.1",
        client_id: Optional[str] = None,
        access_token: Optional[str] = None,
        base_url: Optional[str] = None,
        token_key: str = "default",
        non_interactive: bool = False,
    ):
        self.model_name = model_name
        self.base_url = (base_url or os.environ.get("COPILOT_BASE_URL") or self.DEFAULT_BASE_URL).rstrip("/")
        self.non_interactive = non_interactive or os.environ.get("COPILOT_NON_INTERACTIVE", "").lower() in {
            "1",
            "true",
            "yes",
        }
        self.token_key = token_key
        self.auth_client = DeviceFlowAuthClient(client_id=client_id)
        self._access_token = access_token or os.environ.get("GITHUB_COPILOT_TOKEN") or self._load_token_from_keyring()

    def get_model_name(self) -> str:
        return self.model_name

    def _load_token_from_keyring(self) -> Optional[str]:
        if keyring is None:
            return None
        try:
            return keyring.get_password(self.KEYRING_SERVICE, self.token_key)
        except Exception:
            return None

    def _save_token_to_keyring(self, token: str) -> None:
        if keyring is None:
            return
        try:
            keyring.set_password(self.KEYRING_SERVICE, self.token_key, token)
        except Exception:
            return

    @classmethod
    def copilot_login(
        cls,
        status_callback: Optional[Callable[[str], None]] = None,
        open_browser: bool = True,
        token_key: str = "default",
        prefer_gh_cli: bool = True,
        client_id: Optional[str] = None,
        show_popup: bool = True,
    ) -> Dict[str, Any]:
        existing_token: Optional[str] = None
        if keyring is not None:
            try:
                existing_token = keyring.get_password(cls.KEYRING_SERVICE, token_key)
            except Exception:
                existing_token = None

        if existing_token:
            validation = DeviceFlowAuthClient.validate_token(existing_token)
            if validation.get("valid"):
                if status_callback:
                    status_callback("Using existing keyring token.")
                else:
                    print("[copilot_login] Using existing keyring token.")
                return {
                    "access_token": existing_token,
                    "source": "keyring",
                    "validation": validation,
                }

        result = auth_copilot_login(
            status_callback=status_callback,
            open_browser=open_browser,
            prefer_gh_cli=prefer_gh_cli,
            client_id=client_id,
            show_popup=show_popup,
        )
        token = result.get("access_token")
        if token and keyring is not None:
            try:
                keyring.set_password(cls.KEYRING_SERVICE, token_key, token)
            except Exception:
                pass
        return result

    @classmethod
    def login_with_device_flow(
        cls,
        client_id: Optional[str] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        open_browser: bool = True,
        token_key: str = "default",
        prefer_gh_cli: bool = True,
        show_popup: bool = True,
    ) -> Dict[str, Any]:
        # Backward-compatible alias for older call sites.
        return cls.copilot_login(
            status_callback=status_callback,
            open_browser=open_browser,
            token_key=token_key,
            prefer_gh_cli=prefer_gh_cli,
            client_id=client_id,
            show_popup=show_popup,
        )

    async def get_auth_status_async(self) -> Dict[str, Any]:
        if not self._access_token:
            return {"token_present": False, "token_validation": {"valid": False, "reason": "missing"}}

        validation = await asyncio.to_thread(self.auth_client.validate_token, self._access_token)
        return {"token_present": True, "token_validation": validation}

    async def ensure_authenticated(
        self,
        status_callback: Optional[Callable[[str], None]] = None,
        open_browser: bool = True,
    ) -> Dict[str, Any]:
        status = await self.get_auth_status_async()
        validation = status.get("token_validation", {})
        if validation.get("valid"):
            return status

        if self.non_interactive:
            raise RuntimeError("No valid token and non-interactive mode is enabled.")

        result = await asyncio.to_thread(
            self.copilot_login,
            status_callback,
            open_browser,
        )
        token = result.get("access_token")
        if not token:
            raise RuntimeError("Login flow completed without an access token.")
        self._access_token = token
        self._save_token_to_keyring(token)
        return {"token_present": True, "token_validation": result.get("validation", {"valid": False})}

    async def list_models_async(self) -> List[str]:
        await self.ensure_authenticated()

        def _list() -> List[str]:
            url = f"{self.base_url}/models"
            response = requests.get(
                url,
                headers=self._headers(),
                timeout=30,
            )
            if response.status_code >= 400:
                return self.DEFAULT_MODELS
            payload = response.json() if response.content else {}
            if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                names = [item.get("id") for item in payload["data"] if isinstance(item, dict) and item.get("id")]
                return names or self.DEFAULT_MODELS
            if isinstance(payload, list):
                names = [item.get("id") for item in payload if isinstance(item, dict) and item.get("id")]
                return names or self.DEFAULT_MODELS
            return self.DEFAULT_MODELS

        return await asyncio.to_thread(_list)

    def _headers(self) -> Dict[str, str]:
        if not self._access_token:
            raise RuntimeError("No access token available.")
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "logicore-copilot-sandbox",
        }

    async def chat_async(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        await self.ensure_authenticated()

        payload = {
            "model": model or self.model_name,
            "messages": messages,
            "temperature": temperature,
        }

        def _chat() -> Dict[str, Any]:
            url = f"{self.base_url}/chat/completions"
            response = requests.post(
                url,
                headers=self._headers(),
                data=json.dumps(payload),
                timeout=60,
            )
            if response.status_code == 401:
                return {"error": "unauthorized", "status_code": 401, "payload": response.text}
            if response.status_code >= 400:
                return {
                    "error": "request_failed",
                    "status_code": response.status_code,
                    "payload": response.text,
                }
            return response.json() if response.content else {}

        result = await asyncio.to_thread(_chat)
        if result.get("error") == "unauthorized":
            self._access_token = None
            await self.ensure_authenticated()
            return await self.chat_async(messages=messages, model=model, temperature=temperature)
        return result


def copilot_login(
    status_callback: Optional[Callable[[str], None]] = None,
    open_browser: bool = True,
    token_key: str = "default",
    prefer_gh_cli: bool = True,
    client_id: Optional[str] = None,
    show_popup: bool = True,
) -> Dict[str, Any]:
    """Module-level convenience API: one-call browser login + keyring save."""
    return CopilotProviderSandbox.copilot_login(
        status_callback=status_callback,
        open_browser=open_browser,
        token_key=token_key,
        prefer_gh_cli=prefer_gh_cli,
        client_id=client_id,
        show_popup=show_popup,
    )
