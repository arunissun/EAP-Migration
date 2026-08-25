"""Typer command-line interface for the migration engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import typer

from .case_loader import load_case
from .client import EapApiClient
from .exceptions import EapMigrationError
from .logging import configure_logging, redact
from .orchestrator import MigrationEngine
from .paths import (
    default_artifact_root,
    default_state_root,
    find_repository_root,
    legacy_state_path,
)
from .settings import Environment, Settings, api_base_url
from .state import StateStore, atomic_write_json
from .updates import EapKind, UpdateEngine, load_change_document, load_update_plan

app = typer.Typer(help="IFRC GO EAP migration tool", no_args_is_help=True)
state_app = typer.Typer(help="Inspect or explicitly reset local migration state")
app.add_typer(state_app, name="state")


def print_json(value: Any) -> None:
    typer.echo(json.dumps(value, indent=2, sort_keys=True, default=str))


def make_engine(
    case_path: Path,
    *,
    state_root: Path | None = None,
    artifact_root: Path | None = None,
) -> MigrationEngine:
    settings = Settings()
    case, resolved_path = load_case(case_path, settings)
    token = settings.token_value()
    logger = configure_logging((token,))
    client = EapApiClient(
        token,
        base_url=api_base_url(Environment.STAGE),
        timeout_seconds=settings.timeout_seconds,
        get_retries=settings.get_retries,
    )
    return MigrationEngine(
        case,
        resolved_path,
        client,
        state_root=state_root,
        artifact_root=artifact_root,
        max_file_size_bytes=settings.max_file_size_bytes,
        logger=logger,
    )


@app.command()
def validate(
    case_path: Path = typer.Argument(..., exists=True, readable=True),
    state_root: Path | None = typer.Option(None, "--state-root"),
) -> None:
    """Validate a case and local attachments without network access or writes."""

    settings = Settings()
    case, resolved_path = load_case(case_path, settings)
    # A local validation command does not need a token, but it reports whether
    # network commands will be available without printing the token.
    token_present = bool(settings.api_token and settings.api_token.get_secret_value().strip())
    client = EapApiClient(
        token="local-validation-placeholder",
        base_url=api_base_url(Environment.STAGE),
        timeout_seconds=settings.timeout_seconds,
        get_retries=settings.get_retries,
    )
    try:
        engine = MigrationEngine(
            case,
            resolved_path,
            client,
            state_root=state_root,
            max_file_size_bytes=settings.max_file_size_bytes,
        )
        report = engine.validate_local()
        report["api_token_present"] = token_present
        token = settings.api_token.get_secret_value() if settings.api_token else ""
        print_json(redact(report, (token,)))
    finally:
        client.close()


@app.command()
def plan(
    case_path: Path = typer.Argument(..., exists=True, readable=True),
    environment: Environment = typer.Option(Environment.STAGE, "--environment"),
    state_root: Path | None = typer.Option(None, "--state-root"),
) -> None:
    """Run GET-only discovery and display the redacted migration plan."""

    if environment is not Environment.STAGE:
        raise typer.BadParameter("version one only supports --environment stage")
    engine = make_engine(case_path, state_root=state_root)
    try:
        print_json(engine.client.redact_for_output(engine.plan().to_dict()))
    finally:
        engine.client.close()


@app.command()
def apply(
    case_path: Path = typer.Argument(..., exists=True, readable=True),
    environment: Environment = typer.Option(Environment.STAGE, "--environment"),
    confirm_stage_writes: bool = typer.Option(False, "--confirm-stage-writes"),
    state_root: Path | None = typer.Option(None, "--state-root"),
    artifact_root: Path | None = typer.Option(None, "--artifact-root"),
) -> None:
    """Create a new Under Development registration/application in staging."""

    if environment is not Environment.STAGE:
        raise typer.BadParameter("version one only supports --environment stage")
    engine = make_engine(
        case_path,
        state_root=state_root,
        artifact_root=artifact_root,
    )
    try:
        print_json(
            engine.client.redact_for_output(
                engine.apply(confirm_stage_writes=confirm_stage_writes)
            )
        )
    finally:
        engine.client.close()


@app.command()
def verify(
    case_path: Path = typer.Argument(..., exists=True, readable=True),
    environment: Environment = typer.Option(Environment.STAGE, "--environment"),
    state_root: Path | None = typer.Option(None, "--state-root"),
) -> None:
    """GET and compare a previously applied migration."""

    if environment is not Environment.STAGE:
        raise typer.BadParameter("version one only supports --environment stage")
    engine = make_engine(case_path, state_root=state_root)
    try:
        print_json(engine.client.redact_for_output(engine.verify()))
    finally:
        engine.client.close()


def make_update_client(settings: Settings) -> EapApiClient:
    token = settings.token_value()
    return EapApiClient(
        token,
        base_url=api_base_url(Environment.STAGE),
        timeout_seconds=settings.timeout_seconds,
        get_retries=settings.get_retries,
    )


def repository_output_path(path: Path) -> Path:
    candidate = path.expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (find_repository_root(Path.cwd()) / candidate).resolve()


@app.command("update-plan")
def update_plan(
    changes_path: Path = typer.Argument(..., exists=True, readable=True),
    application_id: int = typer.Option(..., "--application-id"),
    eap_kind: str = typer.Option(..., "--eap-kind"),
    output: Path = typer.Option(Path("artifacts/update-plan.json"), "--output"),
    environment: Environment = typer.Option(Environment.STAGE, "--environment"),
) -> None:
    """Build a GET-only, reviewable final payload for an existing draft."""

    if environment is not Environment.STAGE:
        raise typer.BadParameter("version one only supports --environment stage")
    if eap_kind not in {"simplified", "full"}:
        raise typer.BadParameter("--eap-kind must be simplified or full")
    settings = Settings()
    client = make_update_client(settings)
    try:
        document = load_change_document(changes_path)
        plan = UpdateEngine(
            client, application_id, cast(EapKind, eap_kind)
        ).prepare(document)
        output_path = repository_output_path(output)
        safe_plan = client.redact_for_output(plan.to_dict())
        atomic_write_json(output_path, safe_plan)
        print_json({"plan": safe_plan, "plan_path": str(output_path)})
    finally:
        client.close()


@app.command("update-apply")
def update_apply(
    plan_path: Path = typer.Argument(..., exists=True, readable=True),
    environment: Environment = typer.Option(Environment.STAGE, "--environment"),
    confirm_stage_writes: bool = typer.Option(False, "--confirm-stage-writes"),
) -> None:
    """Apply one reviewed final payload to an unlocked Under Development EAP."""

    if environment is not Environment.STAGE:
        raise typer.BadParameter("version one only supports --environment stage")
    if not confirm_stage_writes:
        raise typer.BadParameter("update-apply requires --confirm-stage-writes")
    settings = Settings()
    client = make_update_client(settings)
    try:
        plan = load_update_plan(plan_path)
        result = UpdateEngine(client, plan.application_id, plan.eap_kind).apply(
            plan, default_artifact_root(find_repository_root(Path.cwd()))
        )
        print_json(result)
    finally:
        client.close()


@app.command("update-verify")
def update_verify(
    plan_path: Path = typer.Argument(..., exists=True, readable=True),
    environment: Environment = typer.Option(Environment.STAGE, "--environment"),
) -> None:
    """Verify an updated EAP using GET requests only."""

    if environment is not Environment.STAGE:
        raise typer.BadParameter("version one only supports --environment stage")
    settings = Settings()
    client = make_update_client(settings)
    try:
        plan = load_update_plan(plan_path)
        print_json(UpdateEngine(client, plan.application_id, plan.eap_kind).verify(plan))
    finally:
        client.close()


@app.command()
def batch(
    cases_path: Path = typer.Argument(..., exists=True, file_okay=False, readable=True),
    environment: Environment = typer.Option(Environment.STAGE, "--environment"),
    apply_writes: bool = typer.Option(False, "--apply"),
    confirm_stage_writes: bool = typer.Option(False, "--confirm-stage-writes"),
    state_root: Path | None = typer.Option(None, "--state-root"),
) -> None:
    """Validate and fully preflight every case before optional sequential writes."""

    if environment is not Environment.STAGE:
        raise typer.BadParameter("version one only supports --environment stage")
    case_paths = sorted(cases_path.glob("*.json"))
    if not case_paths:
        raise typer.BadParameter(f"No JSON cases found in {cases_path}")

    loaded: list[tuple[Path, Any]] = []
    validation_errors: dict[str, str] = {}
    migration_keys: dict[str, Path] = {}
    signatures: dict[str, Path] = {}
    for path in case_paths:
        try:
            settings = Settings()
            case, resolved_path = load_case(path, settings)
            local_client = EapApiClient("local-validation-placeholder")
            try:
                MigrationEngine(
                    case,
                    resolved_path,
                    local_client,
                    state_root=state_root,
                    max_file_size_bytes=settings.max_file_size_bytes,
                ).validate_local()
            finally:
                local_client.close()
            existing_key = migration_keys.get(case.migration_key)
            if existing_key is not None:
                validation_errors[str(path)] = (
                    f"Duplicate migration_key also used by {existing_key}"
                )
                continue
            migration_keys[case.migration_key] = path
            signature = json.dumps(
                case.registration.to_payload(), sort_keys=True, separators=(",", ":")
            )
            existing_signature = signatures.get(signature)
            if existing_signature is not None:
                validation_errors[str(path)] = (
                    f"Duplicate registration signature also used by {existing_signature}"
                )
                continue
            signatures[signature] = path
            loaded.append((path, case))
        except EapMigrationError as exc:
            validation_errors[str(path)] = str(exc)
    if validation_errors:
        print_json({"status": "validation_failed", "errors": validation_errors})
        raise typer.Exit(1)

    if apply_writes and not confirm_stage_writes:
        print_json(
            {
                "status": "write_confirmation_required",
                "message": "Batch writes require both --apply and --confirm-stage-writes",
            }
        )
        raise typer.Exit(1)

    preflight: dict[str, tuple[Path, Any, Any]] = {}
    preflight_output: dict[str, Any] = {}
    preflight_errors: dict[str, str] = {}
    for path, case in loaded:
        engine = make_engine(path, state_root=state_root)
        try:
            planned = engine.plan()
            preflight_output[str(path)] = engine.client.redact_for_output(planned.to_dict())
            if planned.conflicts:
                preflight_errors[str(path)] = "; ".join(planned.conflicts)
            else:
                preflight[str(path)] = (path, case, planned)
        except EapMigrationError as exc:
            preflight_errors[str(path)] = str(exc)
        finally:
            engine.client.close()
    if preflight_errors:
        print_json(
            {
                "status": "preflight_failed",
                "writes_performed": 0,
                "errors": preflight_errors,
                "plans": {key: preflight_output[key] for key in preflight},
            }
        )
        raise typer.Exit(1)

    if not apply_writes:
        print_json(
            {
                "status": "preflight_passed",
                "writes_performed": 0,
                "plans": preflight_output,
            }
        )
        return

    results: dict[str, Any] = {}
    completed: list[str] = []
    for key, (path, _, original_plan) in preflight.items():
        engine = make_engine(path, state_root=state_root)
        try:
            current_plan = engine.plan()
            if _plan_fingerprint(current_plan) != _plan_fingerprint(original_plan):
                raise EapMigrationError(
                    "Batch preflight drift detected before this case; no write was attempted"
                )
            results[key] = engine.client.redact_for_output(
                engine.apply(confirm_stage_writes=True)
            )
            completed.append(key)
        except EapMigrationError as exc:
            print_json(
                {
                    "status": "partial_failure",
                    "completed": completed,
                    "failed_case": key,
                    "error": str(exc),
                    "results": results,
                    "remaining_cases_not_attempted": [
                        item for item in preflight if item not in completed and item != key
                    ],
                }
            )
            raise typer.Exit(1) from None
        finally:
            engine.client.close()
    print_json({"status": "completed", "results": results})


def _plan_fingerprint(plan: Any) -> str:
    value = plan.to_dict()
    value.pop("state", None)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@state_app.command("show")
def state_show(
    migration_key: str = typer.Option(..., "--case"),
    state_root: Path | None = typer.Option(None, "--state-root"),
    case_path: Path | None = typer.Option(None, "--case-path"),
) -> None:
    """Display local state for a migration key."""

    root = (
        state_root.expanduser().resolve()
        if state_root is not None
        else default_state_root(find_repository_root(Path.cwd()))
    )
    state = StateStore(root).load(migration_key)
    if state:
        print_json(state.model_dump(mode="json"))
        return
    result: dict[str, Any] = {"state": None, "state_root": str(root)}
    if case_path is not None:
        legacy = legacy_state_path(case_path, migration_key)
        if legacy.exists() and legacy.parent.resolve() != root:
            result["legacy_state"] = str(legacy)
            result["message"] = (
                "Legacy state was found but not read or moved; use an explicit "
                "--state-root to inspect it."
            )
    print_json(result)


@state_app.command("reset")
def state_reset(
    migration_key: str = typer.Option(..., "--case"),
    confirm: bool = typer.Option(False, "--confirm"),
    state_root: Path | None = typer.Option(None, "--state-root"),
) -> None:
    """Delete only one local state file after explicit confirmation."""

    root = (
        state_root.expanduser().resolve()
        if state_root is not None
        else default_state_root(find_repository_root(Path.cwd()))
    )
    path = StateStore(root).reset(migration_key, confirm=confirm)
    print_json({"reset": str(path)})


def main() -> None:
    try:
        app()
    except EapMigrationError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None
