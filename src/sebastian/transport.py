"""Only the gateway imports this module. No redirects, proxies, or retries."""
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .common import Rejected, canonical

DMR = "http://model-runner.docker.internal"
OPENAI = "https://api.openai.com/v1/responses"
ANTHROPIC = "https://api.anthropic.com/v1/messages"
ALLOWED = {"model-runner.docker.internal", "api.openai.com", "api.anthropic.com"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Rejected("HTTP redirect rejected; credentials and content were not forwarded.")


class Transport:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, url, body=None, headers=None, timeout=180):
        parsed = urlsplit(url)
        if parsed.hostname not in ALLOWED or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise Rejected("Destination is not allowlisted.")
        if parsed.hostname != "model-runner.docker.internal" and parsed.scheme != "https":
            raise Rejected("External traffic requires HTTPS.")
        if parsed.port not in (None, 443):
            raise Rejected("Unexpected destination port.")
        request = urllib.request.Request(url, data=None if body is None else canonical(body).encode(),
                                         headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise Rejected("Provider response exceeds the size limit.")
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise Rejected(f"Provider returned HTTP {exc.code}. Response body omitted to protect sensitive data.") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise Rejected("Provider connection or response failed; no automatic retry was made.") from None


def external_request(model, provider, prompt, output_limit):
    if provider == "openai":
        return OPENAI, {
            "model": model, "input": [{"role": "user", "content": prompt}],
            "max_output_tokens": output_limit, "store": False, "service_tier": "default",
        }
    if provider == "anthropic":
        return ANTHROPIC, {"model": model, "messages": [{"role": "user", "content": prompt}],
                           "max_tokens": output_limit}
    raise Rejected("Unsupported provider.")


def normalize_response(provider, response):
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise Rejected("Usage is missing; the reservation remains unresolved.")
    incoming, outgoing = usage.get("input_tokens"), usage.get("output_tokens")
    if type(incoming) is not int or type(outgoing) is not int or min(incoming, outgoing) < 0:
        raise Rejected("Usage is invalid; the reservation remains unresolved.")
    if provider == "anthropic":
        # Anthropic reports cache tokens separately. Account all at the upper input rate.
        for field in ("cache_creation_input_tokens", "cache_read_input_tokens"):
            value = usage.get(field, 0)
            if type(value) is not int or value < 0:
                raise Rejected("Cache usage is invalid; reservation remains unresolved.")
            incoming += value
        answer = "\n".join(part.get("text", "") for part in response.get("content", []) if part.get("type") == "text")
    else:
        answer = "\n".join(part.get("text", "") for item in response.get("output", [])
                           if item.get("type") == "message" for part in item.get("content", [])
                           if part.get("type") == "output_text")
    return {"text": answer, "input_tokens": incoming, "output_tokens": outgoing,
            "provider_id": response.get("id"), "usage": usage,
            "status": response.get("status", response.get("stop_reason", "unknown"))}
