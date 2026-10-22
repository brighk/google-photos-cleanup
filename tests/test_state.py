from pathlib import Path

from gphotos_cleanup.state import MediaRecord, StateStore


def test_state_round_trip(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)

    record = MediaRecord(
        url="https://photos.google.com/photo/example",
        local_path="/tmp/example.jpg",
        verified=True,
    )
    store.put(record)

    reloaded = StateStore(state_path)
    assert reloaded.get(record.url) == record


def test_mark_deleted(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    store = StateStore(state_path)

    record = MediaRecord(
        url="https://photos.google.com/photo/example",
        local_path="/tmp/example.jpg",
        verified=True,
    )
    store.put(record)
    store.mark_deleted(record.url)

    updated = store.get(record.url)
    assert updated is not None
    assert updated.deleted is True
