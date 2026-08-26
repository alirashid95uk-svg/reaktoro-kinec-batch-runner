from __future__ import annotations

from runner import main as runner_main


def test_runner_dispatches_config_help_without_a_case(capsys) -> None:
    runner_main(["config", "--help", "timestep"])

    assert "solver.timestep.mode" in capsys.readouterr().out
