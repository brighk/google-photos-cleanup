import pytest

from gphotos_cleanup import cli

BASE_ARGS = ["gphotos-cleanup", "--output", "out", "--profile", "profile"]


def _fail_run(**kwargs):
    raise AssertionError("run() must not be called")


def test_delete_and_delete_all_conflict(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", BASE_ARGS + ["--delete", "--delete-all"])
    monkeypatch.setattr(cli, "run", _fail_run)

    with pytest.raises(SystemExit):
        cli.main()


def test_delete_all_cancelled_without_phrase(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", BASE_ARGS + ["--delete-all"])
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    monkeypatch.setattr(cli, "run", _fail_run)

    cli.main()


def test_delete_all_runs_after_phrase(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr("sys.argv", BASE_ARGS + ["--delete-all"])
    monkeypatch.setattr("builtins.input", lambda prompt: cli.DELETE_ALL_PHRASE)
    monkeypatch.setattr(
        cli, "run", lambda **kwargs: calls.append(kwargs) or _empty_result()
    )

    cli.main()

    assert calls[0]["delete_all"] is True
    assert calls[0]["delete"] is False


def test_delete_all_yes_skips_prompt(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr("sys.argv", BASE_ARGS + ["--delete-all", "--yes"])
    monkeypatch.setattr("builtins.input", _fail_run)
    monkeypatch.setattr(
        cli, "run", lambda **kwargs: calls.append(kwargs) or _empty_result()
    )

    cli.main()

    assert calls[0]["delete_all"] is True


def _empty_result():
    from gphotos_cleanup.app import RunResult

    return RunResult(downloaded=0, skipped=0, failed=0, deleted=0)
