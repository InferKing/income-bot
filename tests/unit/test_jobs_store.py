from datetime import UTC, datetime, timedelta

import pytest

from income_tg.jobs import InMemoryJobStore, JsonJobStore, LostJobLeaseError


async def test_json_store_survives_restart(tmp_path) -> None:
    path = tmp_path / "scheduler" / "jobs.json"
    now = datetime(2026, 8, 10, tzinfo=UTC)
    first = JsonJobStore(path)
    await first.ensure("weekly", now)
    lease = await first.claim_due("weekly", now, timedelta(hours=1))
    assert lease is not None
    await first.succeed(lease, now, now + timedelta(days=7), "PROMOTED:v2")

    restored = JsonJobStore(path)
    state = (await restored.list_states())[0]
    assert state.last_result == "PROMOTED:v2"
    assert state.next_run_at == now + timedelta(days=7)
    assert state.lease_token is None


async def test_stale_lease_cannot_complete_new_owner() -> None:
    now = datetime(2026, 8, 10, tzinfo=UTC)
    store = InMemoryJobStore()
    await store.ensure("weekly", now)
    stale = await store.claim_due("weekly", now, timedelta(seconds=1))
    assert stale is not None
    replacement = await store.claim_due("weekly", now + timedelta(seconds=1), timedelta(hours=1))
    assert replacement is not None

    with pytest.raises(LostJobLeaseError):
        await store.succeed(stale, now, now + timedelta(days=7), None)
