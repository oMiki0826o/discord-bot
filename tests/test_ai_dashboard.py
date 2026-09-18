from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.ai import admin_service


def test_dashboard_awaits_async_memory_counts(monkeypatch) -> None:
    count_vectors = AsyncMock(return_value=12)
    count_summaries = AsyncMock(return_value=3)
    monkeypatch.setattr(admin_service.mem_repo, "count_vectors", count_vectors)
    monkeypatch.setattr(admin_service.mem_repo, "count_summaries", count_summaries)
    monkeypatch.setattr(
        admin_service,
        "get_global_stats",
        lambda hours: {
            "total_requests": 5,
            "total_tokens": 100,
            "active_users": 2,
            "error_count": 0,
            "provider_error_count": 0,
            "error_rate": 0.0,
            "cache_hits": 1,
            "by_model": {},
            "estimated_ratio": 0.0,
        },
    )
    monkeypatch.setattr(admin_service, "get_cache_stats", lambda: {"valid": 4})
    monkeypatch.setattr(admin_service, "get_total_memory_count", lambda: 8)
    monkeypatch.setattr(admin_service, "get_total_user_count", lambda: 6)

    result = asyncio.run(
        admin_service.get_dashboard_data(
            SimpleNamespace(guilds=[object(), object()], latency=0.025)
        )
    )

    assert result["vector_count"] == 12
    assert result["summary_count"] == 3
    assert result["memory_count"] == 8
    assert result["guild_count"] == 2
    assert result["latency_ms"] == 25
    count_vectors.assert_awaited_once_with()
    count_summaries.assert_awaited_once_with()
