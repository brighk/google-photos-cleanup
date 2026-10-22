from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class MediaRecord:
    url: str
    local_path: str
    verified: bool
    deleted: bool = False


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._records: dict[str, MediaRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        for item in raw.get("items", []):
            record = MediaRecord(**item)
            self._records[record.url] = record

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "items": [asdict(record) for record in self._records.values()]
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def get(self, url: str) -> MediaRecord | None:
        return self._records.get(url)

    def put(self, record: MediaRecord) -> None:
        self._records[record.url] = record
        self.save()

    def mark_deleted(self, url: str) -> None:
        current = self._records[url]
        self._records[url] = MediaRecord(
            url=current.url,
            local_path=current.local_path,
            verified=current.verified,
            deleted=True,
        )
        self.save()

    def verified_not_deleted(self) -> list[MediaRecord]:
        return [
            record
            for record in self._records.values()
            if record.verified and not record.deleted
        ]

    def all_records(self) -> list[MediaRecord]:
        return list(self._records.values())
