# Passing the old token during `aye auth login` without a new endpoint

## Short answer

Yes — reuse an existing authenticated backend call instead of adding a new endpoint.

The best candidate in the current codebase is:

```http
POST /plugins
```

implemented client-side by:

```python
fetch_plugin_manifest()
```

in `model/api.py`.

That endpoint is already called to fetch plugin metadata/content, and login typically wants to refresh plugins anyway. We can piggyback token replacement metadata on that request.

## Recommended design

When the user runs:

```bash
aye auth login
```

capture the old token before saving the new one, then call the existing `/plugins` endpoint with:

- new token in the `Authorization` header
- old token in the JSON body as `previous_token`

Example request:

```http
POST /plugins
Authorization: Bearer <new-token>
Content-Type: application/json
```

```json
{
  "dry_run": false,
  "previous_token": "old-token-if-present"
}
```

Backend behavior:

- If `previous_token` is present and valid, associate the new token with the same existing user.
- If `previous_token` is missing, invalid, expired, demo, or equal to the new token, ignore it.
- Continue returning the plugin manifest exactly as before.

This keeps the change backward-compatible because older clients will continue sending only:

```json
{
  "dry_run": false
}
```

## Why `/plugins` is the best existing call

Current `model/api.py` already has:

```python
def fetch_plugin_manifest(dry_run: bool = False):
    url = f"{BASE_URL}/plugins"
    payload = {"dry_run": dry_run}
    ...
    resp = client.post(url, json=payload, headers=_auth_headers())
```

This call is a good fit because:

1. It is already authenticated.
2. It is naturally related to login/bootstrap.
3. It does not require creating a new endpoint.
4. It can remain backward-compatible.
5. The backend can perform token-linking as a side effect and still return the same plugin manifest response.

I would avoid using `/invoke_cli` for this because token replacement should not depend on the user sending their first chat prompt. I would also avoid `/feedback` because login should not depend on feedback/exit behavior.

## Client-side changes

### 1. Add token override support to auth headers

Current `_auth_headers()` always reads from stored config via `get_token()`:

```python
def _auth_headers() -> Dict[str, str]:
    token = get_token()
    if not token:
        raise RuntimeError("No auth token – run `aye auth login` first.")
    return {"Authorization": f"Bearer {token}"}
```

For login, we need to authenticate with the new token before it is necessarily stored.

Recommended change:

```python
def _auth_headers(token_override: Optional[str] = None) -> Dict[str, str]:
    token = token_override or get_token()
    if not token:
        raise RuntimeError("No auth token – run `aye auth login` first.")
    return {"Authorization": f"Bearer {token}"}
```

### 2. Extend `fetch_plugin_manifest()`

Change it from:

```python
def fetch_plugin_manifest(dry_run: bool = False):
    url = f"{BASE_URL}/plugins"
    payload = {"dry_run": dry_run}
    ...
    resp = client.post(url, json=payload, headers=_auth_headers())
```

to something like:

```python
def fetch_plugin_manifest(
    dry_run: bool = False,
    *,
    token_override: Optional[str] = None,
    previous_token: Optional[str] = None,
):
    """Fetch the plugin manifest from the server.

    Args:
        dry_run: If True, request a dry-run manifest.
        token_override: Optional token to use in the Authorization header.
            This is useful during login before the new token is stored.
        previous_token: Optional previously configured token. Sent to the
            backend so it can associate token replacement with the same user.
    """
    url = f"{BASE_URL}/plugins"
    payload: Dict[str, Any] = {"dry_run": dry_run}

    active_token = token_override

    if previous_token and previous_token != active_token:
        payload["previous_token"] = previous_token

    verify = _ssl_verify()

    with httpx.Client(timeout=TIMEOUT, verify=verify) as client:
        resp = client.post(
            url,
            json=payload,
            headers=_auth_headers(token_override=token_override),
        )
        _check_response(resp)
        return resp.json()
```

### 3. Capture the old token in `login_flow()`

In `model/auth.py`, do not use `get_token()` to read the old token.

`get_token()` can generate and store a demo token as a side effect. During login, we only want the token that was already configured.

Use:

```python
old_token = get_user_config("token")
old_token = str(old_token).strip() if old_token else None
```

Then prompt for the new token:

```python
token = typer.prompt("Paste your token", hide_input=True).strip()
```

Validate format locally if desired:

```python
if not _is_valid_token(token):
    typer.secho("Invalid token format.", fg=typer.colors.RED)
    raise typer.Exit(1)
```

Then call the existing `/plugins` endpoint before storing the new token:

```python
from aye.model.api import fetch_plugin_manifest

fetch_plugin_manifest(
    dry_run=False,
    token_override=token,
    previous_token=old_token,
)
```

Finally store the token:

```python
store_token(token)
typer.secho("✅ Token saved.", fg=typer.colors.GREEN)
```

## Suggested login flow

Recommended flow:

1. Read current configured token without generating a demo token.
2. Prompt user for new token.
3. Validate new token format locally.
4. Call existing `/plugins` endpoint using new token as auth and old token in payload.
5. If backend succeeds, store the new token locally.
6. If backend fails, do not overwrite the old token.

Pseudo-code:

```python
def login_flow() -> None:
    old_token = get_user_config("token")
    old_token = str(old_token).strip() if old_token else None

    rprint("[yellow]Obtain your personal access token at https://ayechat.ai[/]")
    token = typer.prompt("Paste your token", hide_input=True).strip()

    if not _is_valid_token(token):
        typer.secho("Invalid token format.", fg=typer.colors.RED)
        raise typer.Exit(1)

    try:
        from aye.model.api import fetch_plugin_manifest

        fetch_plugin_manifest(
            dry_run=False,
            token_override=token,
            previous_token=old_token,
        )
    except Exception as e:
        typer.secho(
            f"Login failed: could not verify token with the backend. Existing token was not changed. {e}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    store_token(token)
    typer.secho("✅ Token saved.", fg=typer.colors.GREEN)
```

Use a local import inside `login_flow()` because `model/api.py` imports from `model/auth.py`; a top-level import from `auth.py` back into `api.py` may create a circular import.

## Alternative: store first, then call `/plugins`

Another possible implementation:

1. Read old token.
2. Prompt for new token.
3. Store new token.
4. Call `fetch_plugins(previous_token=old_token)`.

This avoids `token_override`, because `_auth_headers()` will read the new stored token.

However, I recommend the safer version above: call backend first, then store the new token only after success. That prevents replacing a working local token with a bad one if the backend rejects the new token.

## Required backend change

Modify the existing `/plugins` handler to accept an optional field:

```json
{
  "dry_run": false,
  "previous_token": "old-token-or-null"
}
```

Backend logic should be side-effect-only and backward-compatible:

```python
new_token = token_from_authorization_header
old_token = body.get("previous_token")

if old_token and old_token != new_token:
    try_associate_replacement_token(
        old_token=old_token,
        new_token=new_token,
    )

return plugin_manifest_as_before()
```

Important backend behavior:

- Do not fail `/plugins` only because `previous_token` is invalid.
- Do fail if the new token in `Authorization` is invalid.
- Make association idempotent.
- Never log raw tokens.

## Changes to `download_plugins.py`

If the login flow currently calls `fetch_plugins()` rather than `fetch_plugin_manifest()` directly, extend `fetch_plugins()` to pass the old token through:

```python
def fetch_plugins(
    dry_run: bool = True,
    *,
    token_override: Optional[str] = None,
    previous_token: Optional[str] = None,
) -> None:
    ...
    plugins = fetch_plugin_manifest(
        dry_run=dry_run,
        token_override=token_override,
        previous_token=previous_token,
    )
```

Then login can call:

```python
fetch_plugins(
    dry_run=False,
    token_override=token,
    previous_token=old_token,
)
```

This keeps plugin refresh and token association in one existing network call.

## Debug logging

Current debug logging should not print raw tokens.

For `/plugins`, log only:

```text
[DEBUG] Headers: {'Authorization': 'Bearer <token>'}
[DEBUG] Payload includes previous_token: true
```

Do not print the actual `previous_token` value.

## Environment variable caveat

`get_user_config("token")` honors `AYE_TOKEN` because config lookup checks environment variables first.

So if `AYE_TOKEN` is set, it will be treated as the old token.

Also, after saving the new token to `~/.ayecfg`, `AYE_TOKEN` will still override it. Consider warning the user:

```text
AYE_TOKEN is set; the saved token will not be used until the environment variable is removed.
```

## Test plan

Add tests for these cases:

1. `login_flow()` sends old token as `previous_token`.
2. `login_flow()` uses the new token in `Authorization` via `token_override`.
3. No old token means no `previous_token` field is sent.
4. Same old and new token means no `previous_token` field is sent.
5. Backend failure does not call `store_token()`.
6. Successful backend call stores the new token.
7. `get_token()` is not called during login before the new token is stored, to avoid accidental demo token generation.
8. Existing `fetch_plugin_manifest()` calls without new args still produce the old payload shape except for backward-compatible optional behavior.

## Final recommendation

Do not add a new endpoint for this.

Piggyback on the existing `/plugins` call:

- `Authorization: Bearer <new-token>`
- JSON body includes optional `previous_token`

This is small, backward-compatible, and fits the current login/bootstrap flow better than adding a dedicated `/auth/login` endpoint.
