"""CLI-mode writer: Claude with the operator's ANTHROPIC_API_KEY, Gemini when
Claude cannot answer (no key, expired key, rate limit, outage). Says which
model answered."""
from __future__ import annotations

import http.client
import re

from chatcom.adapters.writers import llm_fallback
from chatcom.adapters.writers.base import Draft
from chatcom.core.errors import ChatcomError, redact

DEFAULT_MODEL = "claude-sonnet-5-5"
_ACCOUNT_PROBLEM = re.compile(r"credit balance|billing|quota|API_KEY_INVALID|API key not valid|permission_denied", re.I)
MAX_TOKENS = 6000


class AnthropicWriter:
    mode = "anthropic"

    def __init__(self, model: str | None = None, gemini_model: str | None = None, transport=None):
        self.model = model or DEFAULT_MODEL
        self.gemini_model = gemini_model or llm_fallback.GEMINI_MODEL
        self.transport = transport or llm_fallback.http_transport

    def draft(self, system: str, prompt: str) -> Draft:
        try:
            ans = llm_fallback.ask(prompt, system=system, claude_model=self.model, gemini_model=self.gemini_model,
                                   max_tokens=MAX_TOKENS, transport=self.transport)
        except llm_fallback.BadRequest as e:
            if _ACCOUNT_PROBLEM.search(str(e)) and str(e).startswith("Claude"):
                # An empty credit balance or a revoked key is an outage for us,
                # not a malformed request: let Gemini answer.
                return self._gemini_after(system, prompt, f"Claude account problem: {redact(str(e))[:160]}")
            if _ACCOUNT_PROBLEM.search(str(e)):
                raise ChatcomError("CHATCOM_E_WRITER_UNAVAILABLE", redact(str(e))[:300],
                                   "Check the API key and billing for the provider named.", recovery="ask",
                                   context={"env": "ANTHROPIC_API_KEY, GEMINI_API_KEY"}) from None
            raise ChatcomError("CHATCOM_E_WRITER_BAD_REQUEST", redact(str(e)),
                               "This is a chatcom bug: report it with the run log.", recovery="ask",
                               context={"claude_model": self.model}) from None
        except llm_fallback.Unavailable as e:
            fix = "Set ANTHROPIC_API_KEY (or GEMINI_API_KEY for the fallback) and retry."
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                fix = ("This Python cannot verify HTTPS certificates. Reinstall chatcom so its certifi dependency "
                       "is present (pipx reinstall chatcom), or run Python's 'Install Certificates.command' on macOS.")
            raise ChatcomError("CHATCOM_E_WRITER_UNAVAILABLE", redact(str(e)), fix,
                               recovery="retry",
                               context={"claude_model": self.model, "gemini_model": self.gemini_model,
                                        "env": "ANTHROPIC_API_KEY, GEMINI_API_KEY"}) from None
        except (ValueError, http.client.HTTPException) as e:
            raise ChatcomError("CHATCOM_E_WRITER_UNAVAILABLE", f"the provider sent a reply that is not JSON ({type(e).__name__})",
                               "Retry; a proxy or captive portal may be answering instead of the API.",
                               recovery="retry") from None
        return Draft(ans.text, ans.model, ans.fallback_reason)

    def _gemini_after(self, system: str, prompt: str, reason: str) -> Draft:
        try:
            ans = llm_fallback.ask_gemini(prompt, system=system, model=self.gemini_model,
                                          max_tokens=MAX_TOKENS, transport=self.transport)
        except (llm_fallback.Unavailable, llm_fallback.BadRequest, ValueError, http.client.HTTPException) as e:
            raise ChatcomError("CHATCOM_E_WRITER_UNAVAILABLE", redact(f"{reason}; Gemini: {type(e).__name__}: {e}")[:400],
                               "Check the API keys and billing for both providers.", recovery="ask",
                               context={"env": "ANTHROPIC_API_KEY, GEMINI_API_KEY"}) from None
        return Draft(ans.text, ans.model, reason)
