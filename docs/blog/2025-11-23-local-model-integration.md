---
title: "Beyond the Cloud: Powering Aye Chat with Local and Custom LLMs"
date: 2025-11-23
draft: false
summary: "A technical guide on how Aye Chat prioritizes local and custom LLMs, allowing users to connect to self-hosted endpoints for enhanced privacy and control."
tags: ["llm", "local-ai", "ollama", "integration"]
---

## Overview

While Aye Chat provides a powerful, cloud-based AI experience out of the box, we believe in giving developers ultimate control over their tools. For many, this means having the ability to use custom, fine-tuned models, run LLMs on their own hardware for privacy, or connect to self-hosted inference endpoints to manage costs. To enable this, Aye Chat was designed with a flexible architecture that prioritizes local model execution.

This is made possible by the `LocalModelPlugin`, a component that allows Aye Chat to seamlessly redirect AI requests to any OpenAI-compatible API endpoint. If you configure it, Aye Chat will *always* try your local model first before falling back to the default cloud API. This puts you in the driver's seat, allowing you to choose the brain that powers your AI assistant.

This post is a technical deep-dive into how this local model invocation works, from the initial check in the `llm_invoker` to the specific implementation within the `LocalModelPlugin` that lets you bring your own model.

## Part 1: The Fork in the Road: The Invocation Flow

Every prompt you send in Aye Chat goes through a decision-making process inside the `invoke_llm` function in `aye/controller/llm_invoker.py`. Its first and most important job is to check if you've configured a local model.

Before making any calls to the default Aye Chat API, it queries the plugin system:

```python
# aye/controller/llm_invoker.py

def invoke_llm(...):
    # ...
    with thinking_spinner(console):
        # 1. Try local model first
        local_response = plugin_manager.handle_command("local_model_invoke", {
            "prompt": prompt,
            "model_id": conf.selected_model,
            "source_files": source_files,
            # ...
        })
        
        if local_response is not None:
            return LLMResponse(
                summary=local_response.get("summary", ""),
                updated_files=local_response.get("updated_files", []),
                chat_id=None,
                source=LLMSource.LOCAL
            )
        
        # 2. Fall back to API
        api_resp = cli_invoke(...)
```

This logic is simple but powerful. It sends a `local_model_invoke` command to the `PluginManager`. If any plugin—in this case, the `LocalModelPlugin`—handles the command and returns a valid response, the invocation process stops there. The response is marked with `source=LLMSource.LOCAL` and returned to the user. Only if `local_response` is `None` does the execution continue to the default cloud-based API. This ensures your local configuration is always prioritized.

## Part 2: The Gateway: The `LocalModelPlugin`

The `LocalModelPlugin` (`plugins/local_model.py`) is where the magic happens. It listens for the `local_model_invoke` command and is responsible for routing the request to the correct endpoint based on your environment variables.

Let's focus on the `_handle_openai_compatible` method, which is the primary way to connect to a wide range of local and custom models.

### Configuration via Environment Variables

The plugin first checks for a specific set of environment variables. If they are present, it knows you want to use a custom OpenAI-compatible endpoint.

```python
# aye/plugins/local_model.py

def _handle_openai_compatible(...):
    api_url = os.environ.get("AYE_LLM_API_URL")
    api_key = os.environ.get("AYE_LLM_API_KEY")
    model_name = os.environ.get("AYE_LLM_MODEL", "gpt-3.5-turbo")
    
    if not api_url or not api_key:
        return None
    # ... execution continues
```

-   `AYE_LLM_API_URL`: The full URL to the chat completions endpoint (e.g., `http://localhost:8000/v1/chat/completions`).
-   `AYE_LLM_API_KEY`: The API key for your service. For many local servers, this can be a dummy value like `"not-needed"`.
-   `AYE_LLM_MODEL`: The name of the model you want to use on your local server (e.g., `codellama:7b-instruct`).

If `api_url` or `api_key` are missing, the function returns `None`, and the `llm_invoker` proceeds to the next handler or the default API.

### Constructing and Sending the Request

Once configured, the plugin constructs a payload that mirrors the OpenAI API format. A critical detail is the `response_format` parameter:

```python
# aye/plugins/local_model.py

payload = {
    "model": model_name, 
    "messages": messages, 
    "temperature": 0.7, 
    "max_tokens": LLM_OUTPUT_TOKENS, 
    "response_format": {"type": "json_object"}
}
```

Aye Chat's ability to modify files depends on the AI returning a structured JSON object. By setting `"response_format": {"type": "json_object"}`, we instruct the model to adhere to the required schema. The plugin then sends this payload to your custom URL using `httpx` and parses the response, readying it for the main application.

## Part 3: A Practical Example with Ollama

Let's see how you can use this to run Aye Chat with a local model served by [Ollama](https://ollama.com/) and [LiteLLM](https://github.com/BerriAI/litellm).

1.  **Install and Run Ollama:** Follow the instructions to install Ollama and pull a model.
    ```bash
    ollama pull codellama:7b-instruct
    ```

2.  **Run the LiteLLM Proxy:** LiteLLM can create an OpenAI-compatible server for your Ollama models.
    ```bash
    pip install litellm
    litellm --model ollama/codellama:7b-instruct
    ```
    This will start a server, typically on `http://localhost:8000`.

3.  **Set Your Environment Variables:** In your terminal, configure Aye Chat to use this new local endpoint.
    ```bash
    export AYE_LLM_API_URL="http://localhost:8000/v1/chat/completions"
    export AYE_LLM_API_KEY="not-needed" # LiteLLM doesn't require a key by default
    export AYE_LLM_MODEL="ollama/codellama:7b-instruct"
    ```

4.  **Start Aye Chat:**
    ```bash
    aye chat
    ```

That's it! Aye Chat will now send all its requests to your local Code Llama instance. You'll see the same AI-powered file editing and chat, but now running entirely on your own machine.

## Part 4: Known Issues and What's Next

This system provides a powerful foundation for model flexibility, but there are areas we plan to improve.

### Known Issues

1.  **Configuration is Environment-Based:** Relying solely on environment variables is not user-friendly. There is no in-app way to manage or switch between different local endpoints.
2.  **Sequential Probing:** The plugin checks for different providers (OpenAI-compatible, etc.) in a fixed order. The first one with valid environment variables wins. This lacks flexibility if a user has multiple local configurations set up.
3.  **No Streaming Support:** The plugin currently waits for the full response from the local model before displaying it. This can make the interaction feel slow, especially with larger models.

### What's Next: A More Integrated Experience

Our roadmap is focused on making the use of local models a first-class feature within Aye Chat.

1.  **In-App Model Configuration:** We are planning a `aye model add` command that will allow users to save, name, and switch between different local and custom model endpoints directly from the CLI, persisting the configuration across sessions.

2.  **Streaming Responses:** We will refactor the `LocalModelPlugin` to support streaming (`yield`) responses. This will allow Aye Chat to display the AI's output token-by-token, just like it does with the default API, dramatically improving the user's perception of speed.

3.  **Endpoint Compatibility Testing:** To improve reliability, we plan to add a command like `aye model test <name>` that will send a test prompt to a configured endpoint and verify that it responds correctly and adheres to the required JSON format.

## Conclusion

The `LocalModelPlugin` is a testament to Aye Chat's philosophy of flexibility and developer control. By providing a simple, environment-driven way to connect to any OpenAI-compatible API, we empower users to break free from the cloud and run AI on their own terms. Whether for privacy, customization, or cost, this feature transforms Aye Chat from a service into a truly personal AI development environment.

---
## About Aye Chat

Aye Chat is an open-source, AI-powered terminal workspace that brings the power of AI directly into your command-line workflow. Edit files, run commands, and chat with your codebase without ever leaving the terminal.

Find the project on GitHub: [https://github.com/acrotron/aye-chat](https://github.com/acrotron/aye-chat)
