def test_frontend_public_apis_are_available() -> None:
    from batch_runner.config import CaseConfig, ResolvedCase, load_case, resolve_case
    from batch_runner.outputs import write_kinetic_mapping, write_outputs
    from batch_runner.protocol import ProtocolEmitter, cancellation_requested
    from batch_runner.simulator import (
        execute_solver,
        prepare_simulation,
        preflight_case,
        run_simulation,
        uses_python_rate_callback,
    )

    assert all(
        callable(item)
        for item in (
            CaseConfig,
            ResolvedCase,
            load_case,
            resolve_case,
            write_kinetic_mapping,
            write_outputs,
            ProtocolEmitter,
            cancellation_requested,
            execute_solver,
            prepare_simulation,
            preflight_case,
            run_simulation,
            uses_python_rate_callback,
        )
    )
