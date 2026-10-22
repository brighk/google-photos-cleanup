from pathlib import Path

from gphotos_cleanup.verify import verify_file


def test_verify_file_accepts_non_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"data")

    assert verify_file(path) is True


def test_verify_file_rejects_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    path.touch()

    assert verify_file(path) is False


def test_verify_file_rejects_missing_file(tmp_path: Path) -> None:
    assert verify_file(tmp_path / "missing.jpg") is False
