# Copilot Models Pack Sandbox

This sandbox validates GitHub OAuth device-flow login and Copilot-style model calls before integrating into the main framework.

## What is implemented

- Browser-based login using GitHub CLI (no client id required from user)
- Raw OAuth device-flow fallback for advanced setups (optional client id)
- OAuth token validation via GitHub user API
- Token persistence in OS keyring (if `keyring` is installed)
- Auto-auth on first request
- Model listing and chat completion calls via an OpenAI-compatible endpoint
- One-call login API: `copilot_login()`
- Verification popup on Windows when user action is needed

## Folder layout

- `src/auth.py`: device-flow auth client
- `src/copilot_provider_sandbox.py`: sandbox provider
- `scripts/run_sandbox_demo.py`: interactive demo runner

## Prerequisites

1. Python 3.10+
2. GitHub CLI (`gh`) installed for easiest browser login
2. Install dependencies:

```bash
pip install requests keyring
```

3. Optional env override only (not required):

```bash
set GITHUB_OAUTH_CLIENT_ID=your_client_id_here
```

Optional env vars:

- `GITHUB_COPILOT_TOKEN`: bypass interactive login
- `COPILOT_BASE_URL`: override model API base URL (default: `https://models.github.ai/inference`)
- `COPILOT_MODEL`: default model for demo
- `COPILOT_NON_INTERACTIVE=true`: disable interactive login
- `COPILOT_LOGIN_POPUP=true|false`: enable/disable verification popup (default: true)

## Run

From repo root:

```bash
python sandbox/copilot_models_pack/scripts/run_sandbox_demo.py
```

## One-call login usage

```python
from src import copilot_login

result = copilot_login()
print(result.get("validation", {}).get("login"))
```

This will trigger browser auth automatically on first use and save token in keyring.
On Windows, a popup appears with verification code and URL when authorization is required.

## Validation checklist

1. Fresh login succeeds and token is saved.
2. Restart demo and confirm token reuse without login.
3. Delete keyring token and verify login is prompted again.
4. Set `COPILOT_NON_INTERACTIVE=true` with no token and verify fail-fast behavior.
