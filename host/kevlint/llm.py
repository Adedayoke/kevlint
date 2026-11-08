"""Minimal client for an OpenAI-compatible chat completions endpoint.

Errors carry only a short reason: never a URL, header or response body, so the
API key cannot reach the terminal or a report through one.
"""
import http.client
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

_log = logging.getLogger(__name__)

ENV_BASE_URL = "KEVLINT_LLM_BASE_URL"
ENV_API_KEY = "KEVLINT_LLM_API_KEY"
ENV_MODEL = "KEVLINT_LLM_MODEL"

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1, 2)
MAX_RETRY_AFTER_SECONDS = 5
MAX_RESPONSE_BYTES = 1_000_000
TEMPERATURE = 0
SEED = 42
# Reasoning models spend completion tokens before they write any answer.
MAX_TOKENS = 4000


class LlmError(Exception):
    """No usable response. stop means later requests would fail the same way."""

    def __init__(self, reason, stop=False):
        super().__init__(reason)
        self.reason = reason
        self.stop = stop


@dataclass(repr=False)
class Config:
    base_url: str
    api_key: str
    model: str


def load_config(environ):
    """Return (Config, None), or (None, a one-line problem naming no values)."""
    names = (ENV_BASE_URL, ENV_API_KEY, ENV_MODEL)
    values = [environ.get(name, "").strip() for name in names]
    missing = [name for name, value in zip(names, values) if not value]
    if missing:
        return None, f"{', '.join(missing)} not set"
    if not _allowed_url(values[0]):
        return None, f"{ENV_BASE_URL} must be https (http only for localhost)"
    return Config(*values), None


def _allowed_url(url):
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    if not host:
        return False
    return parts.scheme == "https" or (parts.scheme == "http" and host in LOOPBACK_HOSTS)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib would otherwise resend the Authorization header to the new host."""

    def redirect_request(self, *args, **kwargs):
        return None


def _build_opener(base_url):
    handlers = [_NoRedirect]
    if urlsplit(base_url).hostname in LOOPBACK_HOSTS:
        # The key is unencrypted on this path, so it must not go through a proxy.
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def _retry_after(headers):
    """The Retry-After header in seconds, or None if absent or not a number."""
    try:
        value = float(headers.get("Retry-After"))
    except (AttributeError, TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _content(raw):
    try:
        text = json.loads(raw)["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise LlmError("malformed API response") from None
    if not isinstance(text, str) or not text.strip():
        raise LlmError("the response had no text")
    return text


class LlmClient:
    def __init__(self, config, timeout=TIMEOUT_SECONDS, sleep=time.sleep):
        self.config = config
        self.timeout = timeout
        self.sleep = sleep
        self.opener = _build_opener(config.base_url)

    def _post(self, body):
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "kevlint",
            },
            method="POST",
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            return response.read(MAX_RESPONSE_BYTES)

    def complete(self, system, user):
        """Return the model's text, or raise LlmError after a short retry."""
        body = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": TEMPERATURE,
            "seed": SEED,
            "max_tokens": MAX_TOKENS,
        }
        reason = "no attempt made"
        for attempt in range(MAX_ATTEMPTS):
            wait = None
            try:
                return _content(self._post(body))
            except urllib.error.HTTPError as error:
                status, wait = error.code, _retry_after(error.headers)
                error.close()
                if status == 400 and "seed" in body:
                    del body["seed"]  # Not every endpoint accepts it.
                    _log.debug("HTTP 400 with seed set; retrying without seed")
                    reason = "HTTP 400"
                    continue
                reason = self._retryable_status(status, wait)
            except urllib.error.URLError as error:
                timed_out = isinstance(error.reason, TimeoutError)
                reason = "timed out" if timed_out else "network error"
            except TimeoutError:
                reason = "timed out"
            except (http.client.HTTPException, OSError):
                reason = "network error"
            if attempt + 1 < MAX_ATTEMPTS:
                self.sleep(BACKOFF_SECONDS[attempt] if wait is None else wait)
        raise LlmError(reason, stop=True)

    @staticmethod
    def _retryable_status(status, wait):
        """Return the reason for a status worth retrying; raise for any other."""
        if 300 <= status < 400:
            raise LlmError(f"unexpected redirect (HTTP {status})", stop=True)
        if status in (401, 403):
            raise LlmError(f"not authorized (HTTP {status})", stop=True)
        if status == 429:
            reason = "rate limited (HTTP 429)"
        elif status >= 500:
            reason = f"server error (HTTP {status})"
        else:
            raise LlmError(f"HTTP {status}")
        if wait is not None and wait > MAX_RETRY_AFTER_SECONDS:
            raise LlmError(f"{reason}, retry delay too long", stop=True)
        return reason
