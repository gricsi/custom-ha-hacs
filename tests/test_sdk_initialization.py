"""Exercise first SDK requests in fresh processes, before anything warms its caches."""

from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("mode", ["authenticated", "anonymous", "skip_authentication"])
def test_first_requests_do_not_block_the_event_loop(mode):
    script = textwrap.dedent(
        r"""
        import asyncio
        import builtins
        import importlib
        import json
        import subprocess
        import sys
        import tempfile

        # HA loads the integration before invoking its async entry points.
        import custom_components.lumo as integration
        from custom_components.lumo.config_flow import async_validate_input
        from homeassistant.core import HomeAssistant
        from homeassistant.helpers import httpx_client
        from openai import _base_client

        httpx = getattr(httpx_client, "httpx2", None) or httpx_client.httpx
        original_import = importlib.import_module

        def guarded_import(name, package=None):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return original_import(name, package)
            raise AssertionError(f"import_module on event loop: {name}, {package}")

        def off_loop(function):
            def checked(*args, **kwargs):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    return function(*args, **kwargs)
                raise AssertionError(f"blocking call on event loop: {function.__name__}")
            return checked

        importlib.import_module = guarded_import
        _base_client.platform_headers.cache_clear()
        builtins.open = off_loop(builtins.open)
        subprocess.Popen = off_loop(subprocess.Popen)
        requests = []

        async def respond(request):
            requests.append((request.method, request.url.path))
            if request.method == "GET":
                return httpx.Response(200, json={"object": "list", "data": []})
            payload = json.loads(request.content)
            if payload.get("stream"):
                chunk = {"id": "c", "object": "chat.completion.chunk", "created": 0,
                         "model": "lumo-lite", "choices": [{"index": 0,
                         "delta": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
                return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                      content="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")
            return httpx.Response(200, json={"id": "c", "object": "chat.completion", "created": 0,
                                  "model": "lumo-lite", "choices": [{"index": 0, "finish_reason": "stop",
                                  "message": {"role": "assistant", "content": "ok"}}],
                                  "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

        async def main():
            hass = HomeAssistant(tempfile.mkdtemp())
            try:
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as shared_http:
                    integration.get_async_client = lambda hass: shared_http
                    mode = sys.argv[1]
                    key = None if mode == "anonymous" else "test-key"
                    if mode != "skip_authentication":
                        assert await async_validate_input(hass, key, "https://example.invalid/v1") is bool(key)
                    client = await integration.async_create_client(hass, key, "https://example.invalid/v1")
                    # with_options creates a new SDK client, as the auth probe and model picker do.
                    await client.with_options(timeout=10).models.list()
                    result = await client.chat.completions.create(
                        model="lumo-lite", messages=[{"role": "user", "content": "hello"}], stream=True
                    )
                    chunks = [chunk async for chunk in result]
                    assert chunks[0].choices[0].delta.content == "ok"
                    assert requests[-1] == ("POST", "/v1/chat/completions")
            finally:
                await hass.async_stop(force=True)

        asyncio.run(main())
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, mode],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
