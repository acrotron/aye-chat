import os
from typing import Optional
from pathlib import Path
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import from the aye package (adjust path if needed)
from aye.auth import store_token, delete_token, get_token
from aye.download_plugins import fetch_plugins
from aye.api import cli_invoke, fetch_plugin_manifest, fetch_server_time


def login(token: str) -> None:
    """
    Store the authentication token locally.
    Obtain your token from https://ayechat.ai.
    """
    store_token(token)
    print("✅ Token stored successfully.")


def fetch_plugins() -> None:
    """
    Fetch and download plugins from the remote server (/plugins endpoint).
    """
    token = get_token()
    if not token:
        raise RuntimeError("No token available. Run login() first.")
    fetch_plugins(dry_run=True)
    print("✅ Plugins fetched and updated.")

def chat_invoke(message: str, chat_id: int = -1, model: Optional[str] = None) -> dict:
    """
    Invoke a chat message on the remote server (/invoke_cli endpoint).
    Returns the response from the LLM.
    """
    token = get_token()
    if not token:
        raise RuntimeError("No token available. Run login() first.")
    response = cli_invoke(chat_id=chat_id, message=message, model=model, dry_run=True)
    print("✅ Chat invoke completed.")
    return response


def get_server_time() -> int:
    """
    Fetch the current server timestamp (/time endpoint).
    """
    token = get_token()
    if not token:
        raise RuntimeError("No token available. Run login() first.")
    timestamp = fetch_server_time(dry_run=True)
    print(f"✅ Server time fetched: {timestamp}")
    return timestamp


def logout() -> None:
    """
    Remove the stored token locally (no remote call).
    """
    delete_token()
    print("🔐 Token removed.")


def parallel_workflow(token: str) -> None:
    """
    Run the entire workflow under ThreadPoolExecutor:
    Submit login (wait) → (submit fetch plugins, get server time, chat invoke in parallel; wait) → submit logout (wait).
    """
    try:
        message = "Hello, world! Generate a simple Python function."
        with ThreadPoolExecutor(max_workers=4) as executor:
            # Submit and wait for login (sequential first)
            future_login = executor.submit(login, token)
            future_login.result()

            # Submit parallel API calls
            future_plugins = executor.submit(fetch_plugins)
            future_time = executor.submit(get_server_time)
            future_chat = executor.submit(chat_invoke, message)

            # Wait for parallel tasks with exception handling
            chat_result = None
            for future in [future_plugins, future_time, future_chat]:
                try:
                    result = future.result()  # Raises any exception from the thread
                    if future == future_chat:
                        chat_result = result
                except Exception as e:
                    print(f"Error in parallel task: {e}")
                    # Optionally, cancel other futures or continue

            if chat_result:
                print("Response summary:", chat_result.get('answer_summary', 'No summary'))

            # Submit and wait for logout (sequential last)
            future_logout = executor.submit(logout)
            future_logout.result()

    except Exception as e:
        print(f"Error in workflow: {e}")


def main():
    """
    Sample workflow with entire flow (including login/logout) under ThreadPoolExecutor.
    Replace 'YOUR_TOKEN_HERE' with your actual token.
    """
    token = os.getenv('AYE_TOKEN', 'YOUR_TOKEN_HERE')  # Or prompt for it
    if token == 'YOUR_TOKEN_HERE':
        print("⚠️  Please set your AYE_TOKEN environment variable or replace 'YOUR_TOKEN_HERE'.")
        return

    parallel_workflow(token)


if __name__ == '__main__':
    main()
