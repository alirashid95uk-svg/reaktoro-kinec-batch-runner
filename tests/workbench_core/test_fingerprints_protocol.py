import json
from datetime import datetime, timezone
from decimal import Decimal

from workbench_core.fingerprints import canonical_sha256, scientific_fingerprint
from workbench_core.protocol_reader import ProtocolLineStatus, parse_protocol_line
from workbench_core.schemas.common import CodeIdentity, DependencyIdentity, EnvironmentIdentity


H0 = "0" * 64
H1 = "1" * 64
NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


def identities() -> tuple[DependencyIdentity, CodeIdentity, EnvironmentIdentity]:
    return (
        DependencyIdentity(logical_name="database", sha256=H0, source="local"),
        CodeIdentity(commit="abc123", dirty=False, relevant_source_sha256=H0),
        EnvironmentIdentity(
            python_version="3.11.15",
            reaktoro_version="2.13.0",
            platform="windows",
            environment_spec_sha256=H0,
            package_inventory_sha256=H0,
        ),
    )


def test_scientific_fingerprint_is_canonical_and_excludes_only_approved_operations() -> None:
    dependency, code, environment = identities()
    base = {
        "case": {"name": "sample-a"},
        "paths": {"output_dir": "run-a"},
        "physical": {"pressure_bar": 100.0},
    }
    moved = {
        "physical": {"pressure_bar": 100.0},
        "paths": {"output_dir": "run-b"},
        "case": {"name": "sample-b"},
    }
    first = scientific_fingerprint(
        base,
        dependency_identities=(dependency,),
        code_identity=code,
        environment_identity=environment,
        configuration_schema_version="case-v1",
    )
    second = scientific_fingerprint(
        moved,
        dependency_identities=(dependency,),
        code_identity=code,
        environment_identity=environment,
        configuration_schema_version="case-v1",
    )
    assert first == second
    assert first != scientific_fingerprint(
        {"paths": {"output_dir": "run-b"}, "physical": {"pressure_bar": 101.0}},
        dependency_identities=(dependency,),
        code_identity=code,
        environment_identity=environment,
        configuration_schema_version="case-v1",
    )
    assert first != scientific_fingerprint(
        moved,
        dependency_identities=(dependency.model_copy(update={"sha256": H1}),),
        code_identity=code,
        environment_identity=environment,
        configuration_schema_version="case-v1",
    )
    assert first != scientific_fingerprint(
        moved,
        dependency_identities=(dependency,),
        code_identity=code.model_copy(update={"relevant_source_sha256": H1}),
        environment_identity=environment,
        configuration_schema_version="case-v1",
    )
    assert first != scientific_fingerprint(
        moved,
        dependency_identities=(dependency,),
        code_identity=code,
        environment_identity=environment.model_copy(update={"package_inventory_sha256": H1}),
        configuration_schema_version="case-v1",
    )
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})
    assert canonical_sha256(Decimal("1.0")) == canonical_sha256(Decimal("1.00"))


def test_protocol_parser_retains_unknown_newer_and_malformed_lines() -> None:
    event = {
        "protocol_version": "1.0",
        "event_type": "worker_ready",
        "timestamp_utc": NOW.isoformat(),
        "run_id": "run-1",
        "case_id": "case-1",
        "sequence_number": 1,
        "producer": "worker",
        "payload": {},
    }
    parsed = parse_protocol_line(json.dumps(event))
    assert parsed.status is ProtocolLineStatus.EVENT
    assert parsed.event is not None and parsed.event.sequence_number == 1

    unknown_line = json.dumps({**event, "event_type": "future_event"})
    unknown = parse_protocol_line(unknown_line)
    assert unknown.status is ProtocolLineStatus.UNSUPPORTED_EVENT
    assert unknown.raw_line == unknown_line
    assert unknown.raw_record["event_type"] == "future_event"

    newer = parse_protocol_line(json.dumps({**event, "protocol_version": "2.0"}))
    assert newer.status is ProtocolLineStatus.UNSUPPORTED_VERSION
    malformed = parse_protocol_line('{"protocol_version":"1.0"')
    assert malformed.status is ProtocolLineStatus.MALFORMED
    assert malformed.raw_line == '{"protocol_version":"1.0"'

    wrong_owner = parse_protocol_line(json.dumps({**event, "producer": "controller"}))
    assert wrong_owner.status is ProtocolLineStatus.INVALID_EVENT
