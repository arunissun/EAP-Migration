from pathlib import Path
from types import SimpleNamespace

import pytest
import typer

from eap_migration import cli
from eap_migration.settings import Environment


class FakePlan:
    def __init__(self, marker: str, conflicts: list[str] | None = None) -> None:
        self.marker = marker
        self.conflicts = conflicts or []

    def to_dict(self) -> dict:
        return {"marker": self.marker, "conflicts": self.conflicts}


class FakeEngine:
    apply_calls: list[str] = []
    plan_calls: list[str] = []
    plan_counts: dict[str, int] = {}
    drift_path: str | None = None

    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self.client = SimpleNamespace(
            close=lambda: None,
            redact_for_output=lambda value: value,
        )
        self.calls = 0

    def validate_local(self) -> dict:
        return {"validation": "passed"}

    def plan(self) -> FakePlan:
        self.calls += 1
        self.__class__.plan_calls.append(self.path)
        count = self.__class__.plan_counts.get(self.path, 0) + 1
        self.__class__.plan_counts[self.path] = count
        if self.path.endswith("bad.json"):
            return FakePlan(self.path, ["unsafe remote conflict"])
        marker = self.path
        if self.path == self.__class__.drift_path and count > 1:
            marker += ":drift"
        return FakePlan(marker)

    def apply(self, *, confirm_stage_writes: bool) -> dict:
        self.__class__.apply_calls.append(self.path)
        return {"path": self.path, "confirmed": confirm_stage_writes}


class FakeLocalEngine:
    def __init__(self, *args, **kwargs) -> None:
        self.client = SimpleNamespace(close=lambda: None)

    def validate_local(self) -> dict:
        return {"validation": "passed"}


def fake_loader(path: Path, _settings):
    case = SimpleNamespace(
        migration_key=path.stem,
        registration=SimpleNamespace(to_payload=lambda: {"country": path.stem}),
    )
    return case, path


def invoke_batch(tmp_path: Path, *, apply: bool) -> None:
    cli.batch(
        tmp_path,
        Environment.STAGE,
        apply,
        apply,
        None,
    )


def setup_batch(monkeypatch) -> None:
    monkeypatch.setattr(cli, "load_case", fake_loader)
    monkeypatch.setattr(cli, "MigrationEngine", FakeLocalEngine)
    monkeypatch.setattr(cli, "make_engine", lambda path, **_: FakeEngine(path))
    FakeEngine.apply_calls = []
    FakeEngine.plan_calls = []
    FakeEngine.plan_counts = {}
    FakeEngine.drift_path = None


def test_later_preflight_conflict_causes_zero_writes(monkeypatch, tmp_path: Path) -> None:
    setup_batch(monkeypatch)
    (tmp_path / "good.json").write_text("{}", encoding="utf-8")
    (tmp_path / "bad.json").write_text("{}", encoding="utf-8")

    with pytest.raises(typer.Exit):
        invoke_batch(tmp_path, apply=True)

    assert FakeEngine.apply_calls == []


def test_duplicate_migration_key_causes_zero_remote_plans(monkeypatch, tmp_path: Path) -> None:
    setup_batch(monkeypatch)
    (tmp_path / "one.json").write_text("{}", encoding="utf-8")
    (tmp_path / "two.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "load_case",
        lambda path, settings: (
            SimpleNamespace(
                migration_key="same",
                registration=SimpleNamespace(to_payload=lambda: {"country": "same"}),
            ),
            path,
        ),
    )

    with pytest.raises(typer.Exit):
        invoke_batch(tmp_path, apply=False)

    assert FakeEngine.plan_calls == []


def test_all_preflight_plans_pass_then_cases_apply_sequentially(
    monkeypatch, tmp_path: Path
) -> None:
    setup_batch(monkeypatch)
    paths = [tmp_path / "one.json", tmp_path / "two.json"]
    for path in paths:
        path.write_text("{}", encoding="utf-8")

    invoke_batch(tmp_path, apply=True)

    assert FakeEngine.apply_calls == [str(path) for path in paths]
    assert FakeEngine.plan_calls == [str(paths[0]), str(paths[1]), str(paths[0]), str(paths[1])]


def test_drift_during_replan_stops_before_that_case(monkeypatch, tmp_path: Path) -> None:
    setup_batch(monkeypatch)
    paths = [tmp_path / "one.json", tmp_path / "two.json"]
    for path in paths:
        path.write_text("{}", encoding="utf-8")
    FakeEngine.drift_path = str(paths[1])

    with pytest.raises(typer.Exit):
        invoke_batch(tmp_path, apply=True)

    assert FakeEngine.apply_calls == [str(paths[0])]
