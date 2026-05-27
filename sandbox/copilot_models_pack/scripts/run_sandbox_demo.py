import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.copilot_provider_sandbox import CopilotProviderSandbox


def _status(message: str) -> None:
    if message == "authorization_pending":
        print("[Sandbox Copilot] Waiting for authorization...")
        return
    if message == "slow_down":
        print("[Sandbox Copilot] Slow down requested by GitHub.")
        return
    print(f"[Sandbox Copilot] {message}")


async def main() -> None:
    model = os.environ.get("COPILOT_MODEL", "gpt-4.1")
    provider = CopilotProviderSandbox(model_name=model)

    print("Sandbox provider initialized")
    print(f"Model: {provider.get_model_name()}")

    run_login = input("Run device-flow login now? [Y/n]: ").strip().lower()
    if run_login not in {"n", "no"}:
        result = CopilotProviderSandbox.login_with_device_flow(status_callback=_status, open_browser=True)
        validation = result.get("validation", {}) if isinstance(result, dict) else {}
        print("[Sandbox Copilot] Token saved to keyring")
        if validation.get("login"):
            print(f"[Sandbox Copilot] Authenticated as: {validation['login']}")

    auth = await provider.ensure_authenticated(status_callback=_status, open_browser=True)
    print(f"Token valid: {auth.get('token_validation', {}).get('valid')}")

    models = await provider.list_models_async()
    print("Available models:")
    for index, name in enumerate(models, start=1):
        print(f"  {index}. {name}")

    prompt = input("Enter a test prompt: ").strip() or "Say hello in one short sentence."
    response = await provider.chat_async(
        messages=[
            {"role": "system", "content": "You are a concise helpful assistant."},
            {"role": "user", "content": prompt},
        ]
    )

    print("\nRaw response:")
    print(response)


if __name__ == "__main__":
    asyncio.run(main())
