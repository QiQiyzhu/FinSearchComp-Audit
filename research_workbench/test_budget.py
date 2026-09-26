from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from research_workbench.budget import BudgetError, KEYS, reserve
from research_workbench.config import Settings

try:
    import fakeredis
    import lupa  # noqa: F401 -- explicitly require actual Lua execution
except ImportError:
    fakeredis = None


def settings(hour=10, day=40):
    return SimpleNamespace(redis_url="redis://budget.example.invalid:6379/0",
                           live_requests_per_hour=hour, live_global_per_day=day)


class BudgetClientTest(unittest.TestCase):
    def test_optional_redis_unconfigured_does_not_connect(self):
        with patch("research_workbench.budget._client") as client:
            self.assertEqual(reserve(Settings(), "request-1")["backend"], "sqlite")
            client.assert_not_called()

    def test_reservation_and_reuse_have_distinct_results(self):
        for reason in ("reserved", "reused"):
            with self.subTest(reason=reason), patch("research_workbench.budget._client") as client:
                client.return_value.eval.return_value = [1, reason, 0]
                result = reserve(settings(), "request-1", 2)
                self.assertEqual(result["reserved"], reason == "reserved")
                self.assertEqual(result["reused"], reason == "reused")

    def test_quota_rejection_is_429_with_retry(self):
        for reason in ("day", "hour"):
            with self.subTest(reason=reason), patch("research_workbench.budget._client") as client:
                client.return_value.eval.return_value = [0, reason, 123]
                with self.assertRaises(BudgetError) as raised:
                    reserve(settings(), "request-1")
                self.assertEqual(raised.exception.status_code, 429)
                self.assertEqual(raised.exception.retry_after, 123)

    def test_connection_auth_and_script_errors_fail_closed_without_secret(self):
        with patch("research_workbench.budget._client", side_effect=RuntimeError("redis://username:secret@private-host")):
            with self.assertRaises(BudgetError) as raised:
                reserve(settings(), "request-1")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_bad_or_corrupt_redis_responses_fail_closed(self):
        for response in (None, [], [0, "corrupt", 60], [1, "unexpected", 0], [0, "idempotency_mismatch", 60]):
            with self.subTest(response=response), patch("research_workbench.budget._client") as client:
                client.return_value.eval.return_value = response
                with self.assertRaises(BudgetError) as raised:
                    reserve(settings(), "request-1")
                self.assertEqual(raised.exception.status_code, 503)

    def test_bad_identifier_and_weight_reject_before_connection(self):
        for identifier, units in (("../escape", 1), ("x" * 129, 1), ("valid", 0), ("valid", True), ("valid", 3)):
            with self.subTest(identifier=identifier, units=units), patch("research_workbench.budget._client") as client:
                with self.assertRaises(BudgetError):
                    reserve(settings(), identifier, units)
                client.assert_not_called()


@unittest.skipIf(fakeredis is None, "Install fakeredis[lua] to execute atomic budget script tests")
class BudgetLuaTest(unittest.TestCase):
    def setUp(self):
        self.server = fakeredis.FakeServer()
        self.redis = fakeredis.FakeRedis(server=self.server, decode_responses=True)
        self.client_patch = patch("research_workbench.budget._client", return_value=self.redis)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)

    def timestamp(self):
        seconds, micros = self.redis.time()
        return seconds * 1000 + micros // 1000

    def seed(self, identifier, weight, age_ms):
        self.redis.zadd(KEYS[0], {identifier: self.timestamp() - age_ms})
        self.redis.hset(KEYS[1], identifier, weight)

    def test_actual_lua_reserves_and_idempotently_reuses(self):
        first = reserve(settings(), "same-request", 2)
        second = reserve(settings(), "same-request", 2)
        self.assertTrue(first["reserved"])
        self.assertTrue(second["reused"])
        self.assertEqual(self.redis.zcard(KEYS[0]), 1)
        self.assertEqual(self.redis.hget(KEYS[1], "same-request"), "2")

    def test_atomic_parallel_admission_cannot_overshoot(self):
        def attempt(index):
            try:
                reserve(settings(hour=6, day=10), f"request-{index}", 2)
                return True
            except BudgetError as error:
                self.assertEqual(error.status_code, 429)
                return False
        with ThreadPoolExecutor(max_workers=12) as executor:
            accepted = list(executor.map(attempt, range(20)))
        self.assertEqual(sum(accepted), 3)
        self.assertEqual(sum(int(v) for v in self.redis.hvals(KEYS[1])), 6)

    def test_daily_limit_counts_units_older_than_one_hour(self):
        self.seed("old-request", 2, 2 * 3600 * 1000)
        with self.assertRaises(BudgetError) as raised:
            reserve(settings(hour=10, day=2), "new-request")
        self.assertEqual(raised.exception.code, "BUDGET_DAY_LIMIT")

    def test_hour_window_expires_independently(self):
        self.seed("old-request", 2, 3600 * 1000 + 1000)
        self.assertTrue(reserve(settings(hour=2, day=4), "new-request", 2)["reserved"])
        self.assertEqual(self.redis.zcard(KEYS[0]), 2)

    def test_24_hour_window_prunes_both_indexes(self):
        self.seed("expired-request", 2, 24 * 3600 * 1000 + 1000)
        reserve(settings(hour=2, day=2), "new-request", 2)
        self.assertIsNone(self.redis.zscore(KEYS[0], "expired-request"))
        self.assertIsNone(self.redis.hget(KEYS[1], "expired-request"))
        self.assertEqual(self.redis.zcard(KEYS[0]), 1)
        self.assertGreater(self.redis.ttl(KEYS[0]), 86400)

    def test_missing_counter_fails_closed(self):
        self.redis.zadd(KEYS[0], {"missing-weight": self.timestamp()})
        with self.assertRaises(BudgetError) as raised:
            reserve(settings(), "new-request")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIsNone(self.redis.zscore(KEYS[0], "new-request"))

    def test_idempotency_different_units_is_rejected(self):
        reserve(settings(), "same-request", 1)
        with self.assertRaises(BudgetError) as raised:
            reserve(settings(), "same-request", 2)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(self.redis.hget(KEYS[1], "same-request"), "1")

    def test_new_python_client_keeps_shared_budget(self):
        reserve(settings(hour=1, day=40), "before-web-restart")
        # A separate client to the same Redis server sees the prior reservation.
        other = fakeredis.FakeRedis(server=self.server, decode_responses=True)
        with patch("research_workbench.budget._client", return_value=other):
            with self.assertRaises(BudgetError) as raised:
                reserve(settings(hour=1, day=40), "after-web-restart")
        self.assertEqual(raised.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
