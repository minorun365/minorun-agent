import unittest
from unittest import mock

import anthropic
import httpx
from strands.types.exceptions import ContextWindowOverflowException

import fallback
import usage


def _error(cls, status, message, kind):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls(message, response=response, body={"type": "error", "error": {"type": kind, "message": message}})


CREDIT = lambda: _error(anthropic.BadRequestError, 400,  # noqa: E731
                        "Your credit balance is too low to access the Anthropic API.", "invalid_request_error")
BILLING = lambda: _error(anthropic.APIStatusError, 402, "billing problem", "billing_error")  # noqa: E731
AUTH = lambda: _error(anthropic.AuthenticationError, 401, "invalid x-api-key", "authentication_error")  # noqa: E731
OVERLOADED = lambda: _error(anthropic.InternalServerError, 529, "Overloaded", "overloaded_error")  # noqa: E731
RATE = lambda: _error(anthropic.RateLimitError, 429, "rate limited", "rate_limit_error")  # noqa: E731


class Fake:
    def __init__(self, name, error=None, after=0):
        self.name, self.error, self.after, self.calls = name, error, after, 0
        self.config = {"model_id": name}

    async def stream(self, *args, **kwargs):
        self.calls += 1
        for i in range(2):
            if self.error and i == self.after:
                raise self.error
            yield {"contentBlockDelta": {"delta": {"text": f"{self.name}{i}"}}}
        if self.error and self.after >= 2:
            raise self.error


async def _collect(model):
    return [e["contentBlockDelta"]["delta"]["text"] async for e in model.stream([])]


class Route(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = mock.patch.multiple(fallback._State, disabled_reason=None, busy_until=0.0)
        self.state.start()

    def tearDown(self):
        self.state.stop()

    def model(self, primary):
        return fallback.FallbackModel(lambda creds: primary, Fake("bedrock"))

    async def test_uses_claude_api_first(self):
        m = self.model(Fake("api"))
        self.assertEqual(await _collect(m), ["api0", "api1"])
        self.assertEqual(m.fallback.calls, 0)

    async def test_out_of_credit_switches_to_bedrock_for_good(self):
        for make in (CREDIT, BILLING, AUTH):
            with self.subTest(make=make):
                fallback._State.disabled_reason = None
                primary = Fake("api", make())
                m = self.model(primary)
                self.assertEqual(await _collect(m), ["bedrock0", "bedrock1"])
                self.assertEqual(await _collect(m), ["bedrock0", "bedrock1"])
                self.assertEqual(primary.calls, 1)  # 2回目は Claude API を呼ばない
                self.assertTrue(fallback._State.disabled_reason)

    async def test_federation_rejected_is_permanent_but_outage_is_not(self):
        rejected = anthropic.WorkloadIdentityError("Token exchange failed (HTTP 400): federation rule mismatch")
        outage = anthropic.WorkloadIdentityError("Token exchange failed (HTTP 503): unavailable")
        self.assertTrue(fallback._is_permanent(rejected))
        self.assertFalse(fallback._is_permanent(outage))

    async def test_busy_falls_back_once_then_returns(self):
        for make in (OVERLOADED, RATE, lambda: anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))):
            with self.subTest(make=make):
                fallback._State.busy_until = 0.0
                primary = Fake("api", make())
                m = self.model(primary)
                self.assertEqual(await _collect(m), ["bedrock0", "bedrock1"])
                self.assertIsNone(fallback._State.disabled_reason)
                await _collect(m)
                self.assertEqual(primary.calls, 1)  # 待つ間は Bedrock
                fallback._State.busy_until = 0.0
                primary.error = None
                self.assertEqual(await _collect(m), ["api0", "api1"])

    async def test_failure_after_output_is_not_rerouted(self):
        m = self.model(Fake("api", OVERLOADED(), after=1))
        with self.assertRaises(anthropic.InternalServerError):
            await _collect(m)
        self.assertEqual(m.fallback.calls, 0)

    async def test_context_overflow_goes_to_summarizer(self):
        m = self.model(Fake("api", ContextWindowOverflowException("too long")))
        with self.assertRaises(ContextWindowOverflowException):
            await _collect(m)
        self.assertEqual(m.fallback.calls, 0)

    async def test_missing_credentials_uses_bedrock(self):
        with mock.patch.object(fallback, "credentials", side_effect=RuntimeError("no rule")):
            m = self.model(Fake("api"))
            self.assertEqual(await _collect(m), ["bedrock0", "bedrock1"])
        self.assertEqual(fallback._State.disabled_reason, "credentials_unavailable")

    async def test_switched_off_never_calls_api(self):
        fallback._State.disabled_reason = "switched_off"
        primary = Fake("api")
        self.assertEqual(await _collect(self.model(primary)), ["bedrock0", "bedrock1"])
        self.assertEqual(primary.calls, 0)


class Ledger(unittest.IsolatedAsyncioTestCase):
    async def test_refused_api_call_costs_zero_and_bedrock_is_counted(self):
        async def refused(self, *args, **kwargs):
            raise CREDIT()
            yield  # noqa

        async def bedrock(self, *args, **kwargs):
            yield {"metadata": {"usage": {"inputTokens": 1000, "outputTokens": 100}}}

        ledger = usage.Ledger()
        token = usage.CURRENT.set(ledger)
        state = mock.patch.multiple(fallback._State, disabled_reason=None, busy_until=0.0)
        try:
            with state, mock.patch.object(usage.AnthropicModel, "stream", refused), \
                    mock.patch.object(usage.BedrockModel, "stream", bedrock):
                api = usage.MeteredAnthropicModel(client_args={"api_key": "k"}, model_id="claude-sonnet-5-5", max_tokens=10)
                bed = usage.MeteredModel(model_id="global.anthropic.claude-sonnet-5-5")
                with mock.patch.object(fallback, "credentials", return_value=None):
                    _ = [e async for e in fallback.FallbackModel(lambda creds: api, bed).stream([])]
        finally:
            usage.CURRENT.reset(token)
        result = ledger.snapshot(0)
        self.assertTrue(result["complete"])
        self.assertEqual([c["model"] for c in result["calls"]], ["claude-sonnet-5-5", "global.anthropic.claude-sonnet-5-5"])
        self.assertAlmostEqual(result["usd"], (1000 * 2 + 100 * 10) / 1_000_000)


if __name__ == "__main__":
    unittest.main()
