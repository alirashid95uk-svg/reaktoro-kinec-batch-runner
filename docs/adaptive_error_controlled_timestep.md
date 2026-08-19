# Error-controlled, event-aware adaptive timestepping

This feature adds an opt-in accuracy-controlled path to the existing `adaptive` timestep mode. The legacy feasibility-only behavior remains unchanged when `error_control.enabled: false`.

## Algorithm

For an accepted state \(y_n\) and trial step \(h\), the controller evaluates the interval in two independent ways:

\[
y_F=\Phi_h(y_n),\qquad
y_H=\Phi_{h/2}(\Phi_{h/2}(y_n)).
\]

For a method with demonstrated temporal order \(p\), the Richardson estimate for controlled variable \(i\) is

\[
e_i=\frac{|y_{H,i}-y_{F,i}|}{2^p-1}.
\]

The mixed absolute-relative scale is

\[
T_i=A_i+R|y_{H,i}|,
\qquad
E=\max_i\frac{e_i}{T_i}.
\]

The trial is accepted only when all Reaktoro sub-solves succeed and \(E\le1\). The accepted chemical state is **the genuine two-half-step Reaktoro state** \(y_H\); the algebraically extrapolated state is not injected into Reaktoro.

At startup, or after controller history is reset, an I-controller proposes

\[
h_{n+1}=h_n\left(\frac{s}{E_n}\right)^{0.7/(p+1)}.
\]

With usable error history, the PI proposal is

\[
h_{n+1}=h_n
\left(\frac{s}{E_n}\right)^{(K_I+K_P)/(p+1)}
\left(\frac{s}{E_{n-1}}\right)^{-K_P/(p+1)}.
\]

The implementation starts from \(s=0.8\), \(K_I=0.3\), and \(K_P=0.4\), but these are calibration starting values rather than universal Reaktoro constants. Growth and reduction limiters and `dt_min`/`dt_max` remain active.

If any Reaktoro sub-solve fails, no Richardson estimate is attempted. The accepted state remains untouched and the retry interval becomes

\[
h_{retry}=d_{restart}h,
\]

with a default candidate \(d_{restart}=0.33\).

## Geochemical event limiter

Optional event control augments numerical-error control; it cannot replace it. For selected kinetic minerals, the controller tracks

\[
g_m=n_m \quad\text{(mineral exhaustion)},
\qquad
g_m=SI_m \quad\text{(saturation transition)}.
\]

From two accepted states, a secant extrapolation predicts a future zero crossing. The next trial uses the most restrictive of the PI proposal, event prediction, output time, checkpoint time, and `dt_max`. If a completed trial crosses an event, a linear corrective interpolation shortens the step and retries from the last accepted state.

## Configuration

```yaml
solver:
  timestep:
    mode: adaptive
    time:
      duration_value: 10
      duration_unit: years
      year_definition_days: 365.25
    step_size:
      dt_initial: {value: 1, unit: days}
      dt_min: {value: 1, unit: seconds}
      dt_max: {value: 30, unit: days}
      growth_factor: 2.0      # legacy feasibility-only path
      shrink_factor: 0.5      # legacy feasibility-only path
      max_retries_per_step: 8
    error_control:
      enabled: true
      temporal_order: 1.0     # must be demonstrated by h, h/2, h/4 refinement
      relative_tolerance: 1.0e-3
      species_absolute_tolerance_mol: 1.0e-12
      mineral_absolute_tolerance_mol: 1.0e-12
      controlled_species: []  # empty -> requested species
      controlled_minerals: [] # empty -> all kinetic minerals
      safety_factor: 0.8
      startup_normalized_gain: 0.7
      pi_normalized_integral_gain: 0.3
      pi_normalized_proportional_gain: 0.4
      max_growth_factor: 2.0
      min_reduction_factor: 0.1
      restart_factor: 0.33
    event_control:
      enabled: true
      minerals: []
      mineral_exhaustion: true
      saturation_crossing: true
      mineral_amount_event_tolerance_mol: 1.0e-14
      saturation_index_event_tolerance: 1.0e-8
```

## Scientific basis

The implementation combines established components rather than claiming a new fundamental integration method:

- Belfort, Carrayrou & Lehmann (2007): Richardson step doubling and adaptive temporal-error control in reactive transport / hydrogeochemical codes.
- Kavetski, Binning & Sloan (2002): truncation-error-controlled automatic timestepping for strongly nonlinear subsurface models.
- Söderlind (2002, 2003): feedback-control and digital-filter formulation of stable, smooth adaptive timestep controllers.
- Younes & Ackerer (2010): evidence that nonlinear-solver-performance heuristics do not guarantee temporal accuracy.
- Han, Ren & Younis (2023): combination of local discretization-error control with explicit physical-event detection.
- Sæternes & Cai (2026): modern control-theoretic adaptive timestep implementation in a porous-media simulator, including separate handling of nonlinear-solver failure.

The project-specific contribution is the integration of these ideas around a black-box Reaktoro kinetic solver, with geochemical zero-crossing functions for mineral exhaustion and saturation transitions.

## Required validation before production use

1. Demonstrate the effective temporal order `p` using `h`, `h/2`, and `h/4` refinements.
2. Compare against converged fixed-step reference solutions.
3. Show tolerance convergence as absolute/relative tolerances are tightened.
4. Validate precipitation, dissolution, mineral exhaustion, and SI crossings.
5. Validate startup recovery and repeated rollback with real Reaktoro cases.
6. Compare wall time only at matched numerical accuracy.

Step doubling can require three Reaktoro solves per accepted trial. Runtime improvement is therefore a hypothesis to test, not an assumed property.
