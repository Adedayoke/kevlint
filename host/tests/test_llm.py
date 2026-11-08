import unittest

from kevlint.llm import Config, LlmClient, LlmError, load_config
from tests.fake_llm import FakeLlm, reply

KEY = "sentinel-key-do-not-print"


def client_for(fake, timeout=5):
    sleeps = []
    client = LlmClient(Config(fake.base_url, KEY, "m"), timeout=timeout, sleep=sleeps.append)
    return client, sleeps


class ConfigTest(unittest.TestCase):
    GOOD = {
        "KEVLINT_LLM_BASE_URL": "https://example.test/v1",
        "KEVLINT_LLM_API_KEY": KEY,
        "KEVLINT_LLM_MODEL": "m",
    }

    def test_all_set(self):
        config, problem = load_config(self.GOOD)
        self.assertIsNone(problem)
        self.assertEqual(config.model, "m")

    def test_missing_names_only_the_missing_variable(self):
        env = dict(self.GOOD, KEVLINT_LLM_MODEL="  ")
        config, problem = load_config(env)
        self.assertIsNone(config)
        self.assertEqual(problem, "KEVLINT_LLM_MODEL not set")

    def test_plain_http_is_refused_except_on_localhost(self):
        for url, allowed in (
            ("http://example.test/v1", False),
            ("ftp://example.test", False),
            ("not a url", False),
            ("http://localhost:8000/v1", True),
            ("http://127.0.0.1:8000/v1", True),
        ):
            config, problem = load_config(dict(self.GOOD, KEVLINT_LLM_BASE_URL=url))
            self.assertEqual(config is not None, allowed, url)
            if not allowed:
                self.assertNotIn(url, problem)

    def test_config_repr_hides_the_key(self):
        config, _ = load_config(self.GOOD)
        self.assertNotIn(KEY, repr(config))


class RequestTest(unittest.TestCase):
    def test_sends_low_temperature_seed_and_bearer_key(self):
        with FakeLlm(reply("hello")) as fake:
            client, _ = client_for(fake)
            self.assertEqual(client.complete("sys", "usr"), "hello")
        path, headers, body = fake.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(headers["Authorization"], f"Bearer {KEY}")
        self.assertEqual(body["model"], "m")
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["seed"], 42)
        self.assertEqual(
            body["messages"],
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}],
        )

    def test_retries_once_without_seed_after_400(self):
        with FakeLlm((400, {}, b"{}"), reply("ok")) as fake:
            client, sleeps = client_for(fake)
            self.assertEqual(client.complete("s", "u"), "ok")
        self.assertIn("seed", fake.requests[0][2])
        self.assertNotIn("seed", fake.requests[1][2])
        self.assertEqual(sleeps, [])

    def test_second_400_is_a_failure(self):
        with FakeLlm((400, {}, b"{}")) as fake:
            client, _ = client_for(fake)
            with self.assertRaises(LlmError) as caught:
                client.complete("s", "u")
        self.assertEqual(caught.exception.reason, "HTTP 400")
        self.assertEqual(len(fake.requests), 2)


class RetryTest(unittest.TestCase):
    def test_429_then_success_honors_retry_after(self):
        with FakeLlm((429, {"Retry-After": "2"}, b""), reply("ok")) as fake:
            client, sleeps = client_for(fake)
            self.assertEqual(client.complete("s", "u"), "ok")
        self.assertEqual(sleeps, [2])

    def test_429_without_header_backs_off_then_gives_up(self):
        with FakeLlm((429, {}, b"")) as fake:
            client, sleeps = client_for(fake)
            with self.assertRaises(LlmError) as caught:
                client.complete("s", "u")
        self.assertEqual(caught.exception.reason, "rate limited (HTTP 429)")
        self.assertTrue(caught.exception.stop)
        self.assertEqual(sleeps, [1, 2])
        self.assertEqual(len(fake.requests), 3)

    def test_long_retry_after_gives_up_without_waiting(self):
        with FakeLlm((429, {"Retry-After": "600"}, b"")) as fake:
            client, sleeps = client_for(fake)
            with self.assertRaises(LlmError) as caught:
                client.complete("s", "u")
        self.assertIn("retry delay too long", caught.exception.reason)
        self.assertEqual(sleeps, [])
        self.assertEqual(len(fake.requests), 1)

    def test_timeout_is_retried_then_reported(self):
        with FakeLlm(("hang", 0.4)) as fake:
            client, sleeps = client_for(fake, timeout=0.05)
            with self.assertRaises(LlmError) as caught:
                client.complete("s", "u")
        self.assertEqual(caught.exception.reason, "timed out")
        self.assertEqual(len(sleeps), 2)

    def test_timeout_then_success(self):
        with FakeLlm(("hang", 0.4), reply("ok")) as fake:
            client, _ = client_for(fake, timeout=0.05)
            self.assertEqual(client.complete("s", "u"), "ok")

    def test_server_error_is_retried(self):
        with FakeLlm((503, {}, b""), reply("ok")) as fake:
            client, _ = client_for(fake)
            self.assertEqual(client.complete("s", "u"), "ok")


class FailureTest(unittest.TestCase):
    def test_redirect_is_not_followed(self):
        with FakeLlm((302, {"Location": "/stolen"}, b"")) as fake:
            client, _ = client_for(fake)
            with self.assertRaises(LlmError) as caught:
                client.complete("s", "u")
        self.assertIn("redirect", caught.exception.reason)
        self.assertEqual([r[0] for r in fake.requests], ["/v1/chat/completions"])

    def test_auth_failure_stops_and_never_echoes_the_body(self):
        with FakeLlm((401, {}, f"bad key {KEY}".encode())) as fake:
            client, sleeps = client_for(fake)
            with self.assertRaises(LlmError) as caught:
                client.complete("s", "u")
        self.assertEqual(caught.exception.reason, "not authorized (HTTP 401)")
        self.assertTrue(caught.exception.stop)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertEqual(sleeps, [])

    def test_unusable_success_bodies(self):
        bodies = (
            (b"not json", "malformed API response"),
            (b'{"choices": []}', "malformed API response"),
            (b'{"choices": [{"message": {"content": null}}]}', "the response had no text"),
            (b'{"choices": [{"message": {"content": "  "}}]}', "the response had no text"),
        )
        for payload, wanted in bodies:
            with FakeLlm((200, {}, payload)) as fake:
                client, _ = client_for(fake)
                with self.assertRaises(LlmError) as caught:
                    client.complete("s", "u")
            self.assertEqual(caught.exception.reason, wanted)
            self.assertEqual(len(fake.requests), 1)

    def test_connection_refused_is_reported_as_network_error(self):
        with FakeLlm(reply("x")) as fake:
            url = fake.base_url
        sleeps = []
        client = LlmClient(Config(url, KEY, "m"), timeout=1, sleep=sleeps.append)
        with self.assertRaises(LlmError) as caught:
            client.complete("s", "u")
        self.assertEqual(caught.exception.reason, "network error")
        self.assertTrue(caught.exception.stop)


if __name__ == "__main__":
    unittest.main()
