# Solver Controller API

These modules own solver stage order and timestep acceptance. They are shown
separately because rollback, target landing, and temporal-error decisions have
direct numerical meaning.

## Public solver entry point

::: batch_runner.simulator.solver

## Stage orchestration

::: batch_runner.simulator.solver.execution

## Fixed timestep controller

::: batch_runner.simulator.solver.fixed

## Legacy feasibility-adaptive controller

::: batch_runner.simulator.solver.adaptive

## Richardson error-controlled controller

::: batch_runner.simulator.solver.error_controlled

## Direct Reaktoro solver calls

::: batch_runner.simulator.solver.calls

