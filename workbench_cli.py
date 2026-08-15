"""Qt-free command line for every workbench artifact-changing operation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from workbench_core.comparison import (
    compare_native,
    compatibility_gate,
    reproduce_comparison,
    write_comparison,
)
from workbench_core.datasets import assemble_dataset
from workbench_core.environment import doctor, workbench_doctor, workbench_software_identity
from workbench_core.operations import (
    ProjectControlLock,
    authorise_external_run,
    create_queue,
    execute_queue,
    execute_run,
    finalise_external_run,
    prepare_run,
    prepare_study_sample,
    recover_orphaned_runs,
    synchronise_study_sample,
)
from workbench_core.reports import generate_report, reproduce_report
from workbench_core.result_readers import ResultPackage
from workbench_core.run_index import rebuild_index, search_runs
from workbench_core.studies import generate_study
from workbench_core.validation import validate_case


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Reaktoro Scientific Workbench headless services")
    command.add_argument("--project-root", type=Path, default=Path.cwd())
    subcommands = command.add_subparsers(dest="command", required=True)

    environment = subcommands.add_parser("doctor")
    _solver_arguments(environment)

    validate = subcommands.add_parser("validate")
    validate.add_argument("case", type=Path)
    _solver_arguments(validate)

    prepare = subcommands.add_parser("prepare-run")
    prepare.add_argument("case", type=Path)
    prepare.add_argument("--study-manifest", type=Path)
    prepare.add_argument("--sample-id")
    prepare.add_argument("--scenario-group")
    _solver_arguments(prepare)

    run = subcommands.add_parser("run")
    run.add_argument("case", type=Path)
    run.add_argument("--timeout-s", type=float)
    _solver_arguments(run)

    execute = subcommands.add_parser("execute-run")
    execute.add_argument("run_record", type=Path)
    execute.add_argument("--timeout-s", type=float)
    _solver_arguments(execute)

    authorise = subcommands.add_parser("authorise-run")
    authorise.add_argument("run_record", type=Path)
    _solver_arguments(authorise)

    finalise = subcommands.add_parser("finalise-run")
    finalise.add_argument("run_record", type=Path)
    finalise.add_argument("event_path", type=Path)
    finalise.add_argument("--return-code", type=int, required=True)
    finalise.add_argument("--force-requested", action="store_true")

    queue_create = subcommands.add_parser("queue-create")
    queue_create.add_argument("queue_path", type=Path)
    queue_create.add_argument("run_records", type=Path, nargs="+")
    queue_create.add_argument(
        "--failure-policy",
        choices=("stop_after_failure", "continue_after_failure", "pause_for_decision"),
        default="stop_after_failure",
    )

    queue_run = subcommands.add_parser("queue-run")
    queue_run.add_argument("queue_path", type=Path)
    _solver_arguments(queue_run)

    recovery = subcommands.add_parser("recover")
    recovery.add_argument("--runs-root", type=Path)

    index = subcommands.add_parser("rebuild-index")
    index.add_argument("--runs-root", type=Path)
    index.add_argument("--index", type=Path)

    search = subcommands.add_parser("search-runs")
    search.add_argument("--index", type=Path)
    search.add_argument("--text", default="")
    search.add_argument("--status")
    search.add_argument("--model")
    search.add_argument("--workflow")
    search.add_argument("--study-id")
    search.add_argument("--schema")
    search.add_argument("--started-after")
    search.add_argument("--started-before")

    compare = subcommands.add_parser("compare")
    compare.add_argument("output_dir", type=Path)
    compare.add_argument("quantity")
    compare.add_argument("packages", type=Path, nargs="+")
    compare.add_argument(
        "--mode",
        choices=(
            "native_accepted_grids",
            "initial_state",
            "final_state",
            "exact_common_timestamps",
        ),
        default="native_accepted_grids",
    )
    compare.add_argument("--tolerance-s", type=float, default=0.0)

    compare_check = subcommands.add_parser("compare-check")
    compare_check.add_argument("quantity")
    compare_check.add_argument("packages", type=Path, nargs="+")
    compare_check.add_argument(
        "--mode",
        choices=(
            "native_accepted_grids",
            "initial_state",
            "final_state",
            "exact_common_timestamps",
        ),
        default="native_accepted_grids",
    )
    compare_check.add_argument("--tolerance-s", type=float, default=0.0)

    compare_reproduce = subcommands.add_parser("compare-reproduce")
    compare_reproduce.add_argument("specification", type=Path)
    compare_reproduce.add_argument("--runs-root", type=Path)
    compare_reproduce.add_argument("--output", type=Path)

    study = subcommands.add_parser("study-generate")
    study.add_argument("specification", type=Path)
    _solver_arguments(study)

    dataset = subcommands.add_parser("dataset-assemble")
    dataset.add_argument("output_dir", type=Path)
    dataset.add_argument("packages", type=Path, nargs="+")
    dataset.add_argument("--dataset-type", required=True, choices=(
        "final_state", "fixed_time", "time_dependent_tabular", "trajectory", "failure"
    ))
    dataset.add_argument("--feature", action="append", default=[])
    dataset.add_argument("--target", action="append", default=[])
    dataset.add_argument("--fixed-time-s", type=float)
    dataset.add_argument("--fixed-time-tolerance-s", type=float, default=0.0)
    dataset.add_argument("--group-by", choices=("run_id", "study_id", "scenario_group"), default="run_id")
    dataset.add_argument("--seed", type=int, default=0)
    dataset.add_argument("--duplicate-policy", choices=("error", "exclude", "allow_replicates"), default="error")
    dataset.add_argument("--source-study")
    dataset.add_argument("--source-study-manifest", type=Path)
    dataset.add_argument("--explicit-run-set-id")
    dataset.add_argument("--auditor", type=Path)
    dataset.add_argument("--validity-domain-required", action="store_true")
    dataset.add_argument("--qc-requirements-json", type=Path)
    dataset.add_argument("--split-train", type=float, default=0.7)
    dataset.add_argument("--split-validation", type=float, default=0.15)
    dataset.add_argument("--split-test", type=float, default=0.15)

    report = subcommands.add_parser("report")
    report.add_argument("report_type", choices=("run", "diagnosis", "comparison", "study", "dataset"))
    report.add_argument("output_dir", type=Path)
    report.add_argument("sources", type=Path, nargs="+")
    report.add_argument("--title")
    report_reproduce = subcommands.add_parser("report-reproduce")
    report_reproduce.add_argument("specification", type=Path)
    report_reproduce.add_argument("output_dir", type=Path)
    report_reproduce.add_argument("--source-root", type=Path, action="append", required=True)
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.project_root.resolve()
    try:
        result = _dispatch(args, root)
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def _dispatch(args, root):
    if args.command == "doctor":
        return {
            "workbench": workbench_doctor(root),
            "solver": doctor(root, args.solver_prefix, conda_executable=args.conda),
        }
    if args.command == "validate":
        receipt, path = validate_case(
            args.case,
            root,
            args.solver_prefix,
            root / ".workbench" / "validations",
            conda_executable=args.conda,
        )
        return {"receipt_path": str(path), **receipt.model_dump(mode="json")}
    if args.command == "prepare-run":
        with ProjectControlLock(root):
            if (args.study_manifest is None) != (args.sample_id is None):
                raise ValueError("--study-manifest and --sample-id must be supplied together")
            record = (
                prepare_study_sample(
                    args.study_manifest,
                    args.sample_id,
                    root,
                    args.solver_prefix,
                    conda_executable=args.conda,
                    expected_case=args.case,
                    scenario_group=args.scenario_group,
                )
                if args.study_manifest is not None
                else prepare_run(
                    args.case,
                    root,
                    args.solver_prefix,
                    conda_executable=args.conda,
                    scenario_group=args.scenario_group,
                )
            )
            return record.model_dump(mode="json")
    if args.command == "run":
        with ProjectControlLock(root):
            record = prepare_run(
                args.case, root, args.solver_prefix, conda_executable=args.conda
            )
            if record.state.value != "ready":
                return record.model_dump(mode="json")
            return execute_run(
                Path(record.snapshot_path).parent / "run_record.json",
                root,
                args.solver_prefix,
                conda_executable=args.conda,
                timeout_s=args.timeout_s,
            ).model_dump(mode="json")
    if args.command == "execute-run":
        with ProjectControlLock(root):
            return execute_run(
                args.run_record,
                root,
                args.solver_prefix,
                conda_executable=args.conda,
                timeout_s=args.timeout_s,
            ).model_dump(mode="json")
    if args.command == "authorise-run":
        with ProjectControlLock(root):
            return authorise_external_run(
                args.run_record,
                root,
                args.solver_prefix,
                conda_executable=args.conda,
            ).model_dump(mode="json")
    if args.command == "finalise-run":
        with ProjectControlLock(root):
            return finalise_external_run(
                args.run_record,
                args.event_path,
                return_code=args.return_code,
                force_requested=args.force_requested,
            ).model_dump(mode="json")
    if args.command == "queue-create":
        with ProjectControlLock(root):
            return create_queue(
                args.run_records,
                args.queue_path,
                failure_policy=args.failure_policy,
                project_root=root,
            ).model_dump(mode="json")
    if args.command == "queue-run":
        with ProjectControlLock(root):
            return execute_queue(
                args.queue_path,
                root,
                args.solver_prefix,
                conda_executable=args.conda,
            ).model_dump(mode="json")
    if args.command == "recover":
        with ProjectControlLock(root):
            records = recover_orphaned_runs(args.runs_root or root / "runs")
        result = []
        for record in records:
            item = record.model_dump(mode="json")
            try:
                synchronise_study_sample(root, record)
            except Exception as error:
                if record.study_id and record.sample_id:
                    item["study_manifest_sync_error"] = str(error)
            result.append(item)
        return result
    if args.command == "rebuild-index":
        path = args.index or root / ".workbench" / "run_index.sqlite"
        count = rebuild_index(path, args.runs_root or root / "runs")
        return {"index": str(path.resolve()), "run_count": count}
    if args.command == "search-runs":
        return search_runs(
            args.index or root / ".workbench" / "run_index.sqlite",
            text=args.text,
            status=args.status,
            kinetic_model=args.model,
            workflow_mode=args.workflow,
            study_id=args.study_id,
            output_schema_version=args.schema,
            started_after=args.started_after,
            started_before=args.started_before,
        )
    if args.command == "compare":
        spec, data = write_comparison(
            args.output_dir,
            [ResultPackage(path) for path in args.packages],
            args.quantity,
            mode=args.mode,
            common_time_tolerance_s=args.tolerance_s,
            software_identity=workbench_software_identity(root),
        )
        return {"specification": str(spec), "data": str(data)}
    if args.command == "compare-check":
        packages = [ResultPackage(path) for path in args.packages]
        gate = compatibility_gate(packages, args.quantity)
        if gate["compatible"]:
            try:
                compare_native(
                    packages,
                    args.quantity,
                    mode=args.mode,
                    common_time_tolerance_s=args.tolerance_s,
                )
            except Exception as error:
                gate["compatible"] = False
                gate["errors"].append(str(error))
        return gate
    if args.command == "compare-reproduce":
        return {
            "data": str(
                reproduce_comparison(
                    args.specification,
                    args.runs_root or root / "runs",
                    output_path=args.output,
                )
            )
        }
    if args.command == "study-generate":
        def preflight(case_path: Path):
            receipt, receipt_path = validate_case(
                case_path,
                root,
                args.solver_prefix,
                root / ".workbench" / "validations",
                conda_executable=args.conda,
            )
            return {
                "ready": receipt.ready,
                "receipt_path": str(receipt_path),
                "scientific_fingerprint": receipt.scientific_fingerprint,
            }

        return {"manifest": str(generate_study(args.specification, preflight=preflight))}
    if args.command == "dataset-assemble":
        auditor = args.auditor or (
            root / ".agents" / "skills" / "objective1-output-auditor" / "scripts" / "audit_output_package.py"
        )
        return {
            key: str(value)
            for key, value in assemble_dataset(
                [ResultPackage(path) for path in args.packages],
                args.output_dir,
                dataset_type=args.dataset_type,
                features=args.feature,
                targets=args.target,
                auditor_path=auditor,
                fixed_time_s=args.fixed_time_s,
                fixed_time_tolerance_s=args.fixed_time_tolerance_s,
                group_by=args.group_by,
                seed=args.seed,
                duplicate_policy=args.duplicate_policy,
                validity_domain_required=args.validity_domain_required,
                qc_requirements=(
                    json.loads(args.qc_requirements_json.read_text(encoding="utf-8"))
                    if args.qc_requirements_json
                    else None
                ),
                split_proportions={
                    "train": args.split_train,
                    "validation": args.split_validation,
                    "test": args.split_test,
                },
                source_study=args.source_study,
                source_study_manifest=args.source_study_manifest,
                explicit_run_set_id=args.explicit_run_set_id,
                software_identity=workbench_software_identity(root),
            ).items()
        }
    if args.command == "report":
        return {
            key: str(value)
            for key, value in generate_report(
                args.report_type,
                args.sources,
                args.output_dir,
                title=args.title,
                software_identity=workbench_software_identity(root).model_dump(mode="json"),
            ).items()
        }
    if args.command == "report-reproduce":
        return {
            key: str(value)
            for key, value in reproduce_report(
                args.specification, args.source_root, args.output_dir
            ).items()
        }
    raise AssertionError(args.command)


def _solver_arguments(command):
    command.add_argument("--solver-prefix", type=Path, required=True)
    command.add_argument("--conda", type=Path)


def _print(value):
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
