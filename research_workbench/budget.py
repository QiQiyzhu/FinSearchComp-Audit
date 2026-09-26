"""Optional cross-instance live-call budget, independent of the web filesystem.

Redis admission is atomic and uses its server clock. Keep SQLite admission as a
second local check. Reservations are intentionally not refunded after provider
errors or ambiguous timeouts: a request may already have incurred model cost.
This survives web-service sleep/redeploy, provided Redis itself retains data.
An ephemeral Redis restart or eviction can erase counters; use persistence and
a no-eviction policy for a durable monetary boundary, plus provider spend caps.
"""
from __future__ import annotations

from functools import lru_cache
import re
from typing import Any
from urllib.parse import urlsplit

from .config import Settings


class BudgetError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 503, retry_after: int = 60):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(message)


# The shared hash tag keeps both keys in the same Redis Cluster slot.
KEYS = ("{finagent:live:v1}:reservations", "{finagent:live:v1}:units")
LUA = r"""
local id = ARGV[1]
local weight = tonumber(ARGV[2])
local hour_limit = tonumber(ARGV[3])
local day_limit = tonumber(ARGV[4])
local stamp = redis.call('TIME')
local now = tonumber(stamp[1]) * 1000 + math.floor(tonumber(stamp[2]) / 1000)
local hour_start = now - 3600000
local day_start = now - 86400000
local expired = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', day_start)
for _, member in ipairs(expired) do redis.call('HDEL', KEYS[2], member) end
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', day_start)
local old_weight = redis.call('HGET', KEYS[2], id)
local old_score = redis.call('ZSCORE', KEYS[1], id)
if (old_weight and not old_score) or (old_score and not old_weight) then
    return {0, 'corrupt', 60}
end
if old_weight then
    if tonumber(old_weight) ~= weight then return {0, 'idempotency_mismatch', 60} end
    return {1, 'reused', 0}
end
local records = redis.call('ZRANGE', KEYS[1], 0, -1, 'WITHSCORES')
if #records > 20000 then return {0, 'corrupt', 60} end
local hour_used = 0
local day_used = 0
local hour_oldest = now
local day_oldest = now
for i = 1, #records, 2 do
    local unit = tonumber(redis.call('HGET', KEYS[2], records[i]))
    if not unit or unit < 1 or unit > 2 then return {0, 'corrupt', 60} end
    local score = tonumber(records[i + 1])
    day_used = day_used + unit
    day_oldest = math.min(day_oldest, score)
    if score > hour_start then
        hour_used = hour_used + unit
        hour_oldest = math.min(hour_oldest, score)
    end
end
if day_used + weight > day_limit then
    return {0, 'day', math.max(1, math.ceil((day_oldest + 86400000 - now) / 1000))}
end
if hour_used + weight > hour_limit then
    return {0, 'hour', math.max(1, math.ceil((hour_oldest + 3600000 - now) / 1000))}
end
redis.call('ZADD', KEYS[1], now, id)
redis.call('HSET', KEYS[2], id, weight)
redis.call('EXPIRE', KEYS[1], 90000)
redis.call('EXPIRE', KEYS[2], 90000)
return {1, 'reserved', 0, day_used + weight, hour_used + weight}
"""


@lru_cache(maxsize=8)
def _client(redis_url: str):
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    parsed = urlsplit(redis_url)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError("unsupported Redis configuration")
    # Do not retry a mutation transparently. An ambiguous timeout consumes a
    # conservative reservation; application retries can reuse the identifier.
    return redis.Redis.from_url(redis_url, socket_timeout=3, socket_connect_timeout=3,
                                decode_responses=True, health_check_interval=30,
                                retry=Retry(NoBackoff(), 0), max_connections=8)


def reserve(settings: Settings, identifier: str, units: int = 1) -> dict[str, Any]:
    """Reserve a non-demo admission, or leave admission to SQLite when disabled.

    Call after local idempotency/queue/quota checks and before creating a paid
    job. Both rolling global windows count issuer-analysis units (one or two).
    The identifier must be stable when retrying the same logical reservation.
    """
    redis_url = getattr(settings, "redis_url", "")
    if not redis_url:
        return {"backend": "sqlite", "reserved": False, "reused": False}
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
        raise BudgetError("BUDGET_IDENTIFIER", "研究预算标识无效。")
    if type(units) is not int or units not in {1, 2}:
        raise BudgetError("BUDGET_UNITS", "研究预算消耗单位无效。")
    try:
        result = _client(redis_url).eval(LUA, len(KEYS), *KEYS, identifier, units,
                                        settings.live_requests_per_hour, settings.live_global_per_day)
        if not isinstance(result, (list, tuple)) or len(result) < 3:
            raise ValueError("invalid Redis response")
        accepted = int(result[0])
        reason = result[1].decode() if isinstance(result[1], bytes) else str(result[1])
        retry_after = max(1, min(86400, int(result[2])))
    except Exception:
        # Redis exceptions can include credential-bearing URLs. Never return or
        # chain provider exception text into API responses or application logs.
        raise BudgetError("BUDGET_UNAVAILABLE", "共享研究预算服务暂不可用，付费研究尚未启动。") from None
    if accepted == 1 and reason in {"reserved", "reused"}:
        return {"backend": "redis", "reserved": reason == "reserved", "reused": reason == "reused"}
    if reason == "day":
        raise BudgetError("BUDGET_DAY_LIMIT", "公开研究服务已达到过去 24 小时的总额度，请稍后重试。", 429, retry_after)
    if reason == "hour":
        raise BudgetError("BUDGET_HOUR_LIMIT", "公开研究服务已达到过去一小时的总额度，请稍后重试。", 429, retry_after)
    raise BudgetError("BUDGET_UNAVAILABLE", "共享研究预算状态需要核验，付费研究尚未启动。")
