---
title: "Error-Controlled, Event-Aware Adaptive Time Stepping for Reaktoro Geochemical Kinetics"
aliases:
  - "Adaptive Time Stepping for Reaktoro"
  - "Richardson–PI Geochemical Timestep Controller"
tags:
  - PhD
  - Reaktoro
  - geochemistry
  - kinetics
  - adaptive-timestepping
  - numerical-methods
  - reactive-transport
status: revised-concept-design
created: 2026-08-19
updated: 2026-08-19
---

# Error-Controlled, Event-Aware Adaptive Time Stepping for Reaktoro Geochemical Kinetics

> [!abstract]
> The revised scheme combines **Richardson step-doubling error estimation**, a **simple error-based I-controller**, optional later **PI smoothing**, and **geochemical event detection** around Reaktoro's kinetic solver.  
>
> The core refinement is that the temporal-error controller should act primarily on **kinetic reaction progress / mineral amounts**, while aqueous chemistry, mass balance, and other state quantities remain independent diagnostic or admissibility checks.

---

## 1. Objective

The aim is to make long kinetic geochemical simulations **faster without sacrificing controlled numerical accuracy**.

A simple feasibility-driven controller asks:

$$
\text{Can Reaktoro solve this timestep?}
$$

The revised controller asks:

$$
\boxed{
\text{Can Reaktoro solve it?}
\;\land\;
\text{Is the temporal error acceptable?}
\;\land\;
\text{Are important geochemical transitions adequately resolved?}
}
$$

The central distinction is that **solver convergence is not the same as temporal accuracy**.

---

## 2. Why the Architecture Was Revised

A review of geochemical time-stepping literature shows a progression from:

1. **solver-convergence and reaction-change heuristics**;
2. **adaptive truncation-error control**;
3. **stiff implicit integration with adaptive internal timesteps**;
4. **reaction-extent and mineral-event based controls**;
5. **physics-aware/event-aware adaptive stepping**.

This literature supports the overall design, but suggests three refinements.

### Revision 1 — control kinetic progress first

The primary Richardson error vector should be based on:

- kinetic reaction extents, where accessible; or
- kinetic mineral amounts as the practical equivalent.

Aqueous quantities such as pH and dissolved species should initially remain **diagnostic/admissibility variables**, not equal members of the numerical-error norm.

### Revision 2 — use an I-controller first

The first scientifically defensible implementation should use:

$$
\boxed{
\text{Richardson LTE}
+
\text{simple error-based I-controller}
+
\text{geochemical event caps}
}
$$

A PI/digital-filter controller should be introduced only after the Richardson error history is shown to behave predictably.

### Revision 3 — distinguish hard and soft events

Events should be separated into:

- **hard events** requiring localisation or rollback;
- **soft events** that merely cap the proposed timestep.

---

## 3. Reaktoro-Specific Numerical Basis

The current Reaktoro kinetic formulation advances the kinetic system over a user-supplied interval $\Delta t$ through a constrained nonlinear solve.

Conceptually, the kinetic reaction-progress constraint has the form

$$
\boxed{
\Delta \boldsymbol{\xi}
-
\Delta t\,\mathbf M\,\mathbf r
=
0
}
$$

where:

| Symbol | Meaning |
|---|---|
| $\Delta\boldsymbol{\xi}$ | Reaction-extent increment over the requested timestep |
| $\Delta t$ | Requested external timestep |
| $\mathbf r$ | Current reaction-rate vector |
| $\mathbf M$ | Stoichiometric coupling matrix |

This means the external timestep itself directly enters the nonlinear kinetic constraint.

> [!important]
> Reaktoro therefore does **not** provide the outer workflow with an explicit user-facing temporal-error estimate for the requested timestep. This makes an external full-step / two-half-step comparison a meaningful way to assess temporal resolution.

Reaktoro also has a native very-short first-step preconditioning mechanism.

The revised startup architecture should therefore preserve native Reaktoro preconditioning and place the new controller **outside** it:

$$
\boxed{
\text{Reaktoro first-step preconditioning}
\rightarrow
\text{outer adaptive trial}
\rightarrow
\text{error / failure control}
}
$$

---

## 4. Richardson Step-Doubling Error Estimation

Let the last accepted Reaktoro state be

$$
\mathbf y_n
$$

at time

$$
t_n
$$

and let the proposed external timestep be

$$
h.
$$

Solve the same interval in two independent ways.

### Full-step trajectory

$$
\mathbf y_F
=
\Phi_h(\mathbf y_n)
$$

### Two-half-step trajectory

$$
\mathbf y_{H1}
=
\Phi_{h/2}(\mathbf y_n)
$$

followed by

$$
\mathbf y_H
=
\Phi_{h/2}(\mathbf y_{H1}).
$$

Both end at

$$
t_{n+1}=t_n+h.
$$

Their disagreement provides an estimate of temporal discretisation error.

---

## 5. Primary Controlled Variables

### Preferred variable: reaction extent

For kinetic reaction $j$:

$$
\boxed{
e_j
=
\frac{
\left|
\xi_{H,j}-\xi_{F,j}
\right|
}{
2^p-1
}
}
$$

where:

| Symbol | Meaning |
|---|---|
| $\xi_{F,j}$ | Reaction extent after one full step |
| $\xi_{H,j}$ | Reaction extent after two half steps |
| $p$ | Demonstrated temporal order |
| $e_j$ | Richardson temporal-error estimate |

### Practical fallback: mineral amount

If reaction extent is not conveniently exposed:

$$
\boxed{
e_j
=
\frac{
\left|
n_{H,j}-n_{F,j}
\right|
}{
2^p-1
}
}
$$

where $n_j$ is the amount of kinetic mineral $j$.

> [!recommendation]
> For the first Reaktoro implementation, **kinetic mineral amounts are the preferred practical controlled variables** unless reaction extents can be accessed cleanly and consistently.

---

## 6. Error Scaling and Acceptance

A geochemical system may contain minerals spanning many orders of magnitude, including minerals starting at zero.

Use a mixed absolute-relative tolerance with a reference floor:

$$
\boxed{
T_j
=
A_j
+
R_j
\max
\left(
|n_{H,j}|,
n_{\mathrm{floor},j}
\right)
}
$$

where:

| Symbol | Meaning |
|---|---|
| $A_j$ | Absolute tolerance |
| $R_j$ | Relative tolerance |
| $n_{\mathrm{floor},j}$ | Reference floor preventing singular relative error near zero |
| $T_j$ | Permitted temporal error for mineral/reaction $j$ |

The normalized error is

$$
E_j
=
\frac{e_j}{T_j}.
$$

Use the conservative global criterion

$$
\boxed{
E
=
\max_j(E_j).
}
$$

Accept the timestep when

$$
\boxed{
E\le1.
}
$$

Reject it when

$$
E>1.
$$

### What should *not* be in the primary LTE vector initially

The following should remain separate from the Richardson error measure:

- pH;
- aqueous species concentrations;
- saturation indices;
- total element conservation;
- charge balance;
- small negative numerical noise.

These quantities remain scientifically important, but should initially act as **diagnostics, admissibility checks, or event indicators**.

---

## 7. Which State Is Accepted?

If the Richardson criterion passes:

$$
\boxed{
\mathbf y_{n+1}
=
\mathbf y_H.
}
$$

That is, retain the state generated by **two genuine Reaktoro half-step solves**.

Do not initially use the algebraically extrapolated state

$$
\mathbf y_{\mathrm{ext}}
=
\mathbf y_H
+
\frac{
\mathbf y_H-\mathbf y_F
}{
2^p-1
}
$$

as the new ChemicalState.

An algebraically extrapolated chemical state may not satisfy the nonlinear equilibrium constraints enforced by Reaktoro.

> [!recommendation]
> Use Richardson **to estimate error**, not to manufacture a new chemical state.

---

## 8. Temporal Order Must Be Demonstrated

The Richardson estimator requires a meaningful temporal order $p$.

Do not hard-code

$$
p=1
$$

without evidence.

Measure the effective order using a refinement sequence:

$$
h,
\qquad
\frac{h}{2},
\qquad
\frac{h}{4}.
$$

A first-order behaviour is plausible for the current implicit kinetic stepping structure, but it must be demonstrated over representative geochemical regimes.

Required tests should include:

- rapid initial CO$_2$ acidification;
- carbonate dissolution;
- slow silicate dissolution;
- secondary-mineral precipitation;
- near-equilibrium kinetics;
- mineral exhaustion.

---

## 9. Core Timestep Controller — Stage 1

The first production candidate should use a simple error-based integrating controller.

Let

$$
k=p+1.
$$

Then:

$$
\boxed{
h_{n+1}
=
s\,h_n
E_n^{-1/k}
}
$$

where:

| Symbol | Meaning |
|---|---|
| $h_n$ | Current accepted timestep |
| $E_n$ | Current normalized temporal error |
| $s$ | Safety factor, with $0<s<1$ |
| $k=p+1$ | Error-scaling exponent |

Enforce:

$$
h_{\min}
\le
h_{n+1}
\le
h_{\max}.
$$

Also impose bounded growth:

$$
g_{\min}
\le
\frac{h_{n+1}}{h_n}
\le
g_{\max}.
$$

> [!important]
> This **I-controller is the recommended first implementation**. It is directly tied to the measured numerical error and is easier to validate than a higher-order feedback controller.

---

## 10. Optional Stage 2 — PI Smoothing

After the Richardson estimator and I-controller are validated, introduce a PI controller to smooth timestep evolution.

A generic PI form is

$$
\boxed{
h_{n+1}
=
h_n
\left(
\frac{s}{E_n}
\right)^{k_I+k_P}
\left(
\frac{s}{E_{n-1}}
\right)^{-k_P}
}
$$

where:

| Symbol | Meaning |
|---|---|
| $E_{n-1}$ | Previous accepted normalized error |
| $k_I$ | Integral gain |
| $k_P$ | Proportional gain |

The PI controller is an **efficiency and smoothness optimisation**, not the basis of numerical validity.

> [!note]
> After repeated rejected steps or abrupt chemical events, PI history should be reset and the controller should temporarily fall back to the simpler I-controller.

---

## 11. Solver-Failure Recovery

Richardson error cannot be calculated when a Reaktoro trial solve itself fails.

Use a separate failure path:

$$
\boxed{
\text{solver failure}
\rightarrow
\text{restore last accepted state}
\rightarrow
h_{\mathrm{retry}}
=
d_{\mathrm{restart}}\,h
}
$$

with

$$
0<d_{\mathrm{restart}}<1.
$$

This mechanism is distinct from temporal-error rejection.

### Failure path

```text
Reaktoro trial fails
        ↓
restore accepted state
        ↓
emergency timestep reduction
        ↓
retry
```

This is especially important during rapid initial CO$_2$–brine–mineral transients.

> [!caution]
> If repeated reduction reaches $h_{\min}$ and the solve still fails, the problem should be treated as a chemistry/solver/configuration failure rather than as a timestep-control problem.

---

## 12. Geochemical Event Layer

The event layer supplements temporal-error control.

### Hard events

Hard events should trigger localisation, rollback, or explicit landing.

Examples:

- complete mineral exhaustion;
- phase disappearance;
- externally imposed condition change.

For mineral $m$:

$$
\boxed{
g_m=n_m
}
$$

with event condition

$$
n_m=0.
$$

### Soft events

Soft events should initially **cap the next timestep** rather than force an exact event landing.

Examples:

- saturation-index crossing;
- first indication of secondary-mineral precipitation;
- rapid pH movement;
- rapidly increasing reaction rate.

For saturation:

$$
\boxed{
g_m=SI_m
}
$$

with transition

$$
SI_m=0.
$$

> [!important]
> $SI=0$ is a useful thermodynamic transition indicator, but it is not universally identical to the physical onset of precipitation because nucleation and kinetic laws may delay actual mineral formation.

---

## 13. Event Prediction

Using two accepted states,

$$
(t_{n-1},g_{n-1})
$$

and

$$
(t_n,g_n),
$$

a simple linear zero-crossing estimate is

$$
\boxed{
t_{\mathrm{event}}
=
t_n
-
g_n
\frac{
t_n-t_{n-1}
}{
g_n-g_{n-1}
}
}
$$

and

$$
h_{\mathrm{event}}
=
t_{\mathrm{event}}-t_n.
$$

The trial timestep becomes

$$
\boxed{
h_{\mathrm{trial}}
=
\min
\left(
h_{\mathrm{controller}},
h_{\mathrm{event}},
h_{\mathrm{output}},
h_{\mathrm{checkpoint}},
h_{\max}
\right)
}
$$

for valid future event predictions.

After a hard event:

1. accept/localise the event state;
2. reset error-controller history;
3. restart with the simpler I-controller.

---

## 14. Revised Recommended Architecture

> [!summary]
> **This is the recommended architecture for implementation.**

```text
Reaktoro native first-step preconditioning
                    │
                    ▼
        Last accepted state y_n
                    │
                    ▼
        Choose candidate timestep h
                    │
                    ▼
   Apply output/checkpoint/final-time caps
                    │
                    ▼
       Apply geochemical event cap
                    │
                    ▼
     ┌───────────────────────────────┐
     │ One full Reaktoro solve over h│ → y_F
     └───────────────────────────────┘
                    +
     ┌───────────────────────────────┐
     │ Two Reaktoro solves over h/2  │ → y_H
     └───────────────────────────────┘
                    │
                    ▼
 Richardson error on reaction extent
       or kinetic mineral amounts
                    │
                    ▼
              Is E ≤ 1?
            ┌───────┴───────┐
          YES               NO
           │                 │
           ▼                 ▼
 Scientific/admissibility   Rollback
       checks                │
           │                 ▼
           ▼            Reduce h from
      Accept y_H          error estimate
           │                 │
           ▼                 └──► Retry
   Advance accepted time
           │
           ▼
    I-controller chooses
        next timestep
           │
           ▼
 Geochemical event limiter
           │
           ▼
        Next trial
```

If **any Reaktoro solve fails**:

```text
solver failure
→ restore last accepted state
→ emergency shrink
→ retry
```

After the core method has been validated:

```text
I-controller
     ↓
optional PI/digital-filter smoothing
```

---

## 15. What Each Layer Controls

| Layer | Primary purpose |
|---|---|
| Reaktoro nonlinear solve | Feasibility of the chemical kinetic step |
| Richardson comparison | Temporal discretisation error |
| Absolute/relative tolerance | Required numerical accuracy |
| I-controller | Error-based selection of next timestep |
| PI controller | Optional smoothing and efficiency improvement |
| Hard event detector | Exact or near-exact localisation of major phase/mineral transitions |
| Soft event detector | Conservative timestep cap near rapid chemistry |
| Mass/conservation monitor | Independent scientific integrity |
| Output/checkpoint limiter | Reproducible scheduled timestamps |

---

## 16. Expected Value to the Project

### Controlled numerical accuracy

A successful nonlinear solve is no longer sufficient for acceptance.

The timestep must also satisfy:

$$
E\le1.
$$

### Better startup robustness

Very rapid early reactions can force automatic timestep reduction while retaining Reaktoro's native first-step preconditioning.

### Long-horizon efficiency

The controller should naturally move from:

$$
\text{small early timesteps}
\rightarrow
\text{larger late timesteps}
$$

as the system becomes smoother.

### More defensible AI training data

Simulations can be generated according to a consistent numerical-error tolerance rather than a manually chosen timestep.

### Better treatment of mineral transitions

Hard and soft geochemical event controls reduce the risk of stepping across important precipitation, saturation, or exhaustion behaviour.

---

## 17. Computational Cost

Richardson step doubling can require:

$$
1\text{ full solve}
+
2\text{ half solves}
=
3\text{ Reaktoro solves per trial}.
$$

Therefore the method is not automatically faster.

It provides value only if the larger trustworthy timesteps enabled during smooth periods compensate for the additional trial solves.

The correct benchmark is:

$$
\boxed{
\text{adaptive runtime at error }\varepsilon
\quad\text{vs}\quad
\text{fixed-step runtime at approximately the same error }\varepsilon
}
$$

—not simply adaptive runtime versus fixed runtime.

---

## 18. Novelty

The individual ingredients are established:

- adaptive kinetic integration;
- Richardson error estimation;
- error-based timestep control;
- mineral/reaction-progress limits;
- physics-aware event detection.

The potentially distinctive contribution is their **Reaktoro-specific external integration**:

$$
\boxed{
\text{Reaktoro implicit kinetic step}
+
\text{external Richardson LTE}
+
\text{reaction/mineral-based error control}
+
\text{geochemical hard/soft events}
}
$$

with validation for CO$_2$–brine–mineral batch simulations and large AI-training simulation campaigns.

### Defensible novelty statement

> **A Reaktoro-specific, geochemistry-aware integration of established error-controlled and event-aware adaptive timestep methods for kinetic CO₂–brine–mineral simulations.**

### Claims to avoid

Do not currently claim:

> “A fundamentally novel adaptive timestep algorithm.”

or:

> “The first adaptive geochemical timestep controller.”

A stronger first-of-kind claim requires a dedicated systematic novelty search across literature and software.

---

## 19. Required Validation Before Production Use

### A. Demonstrate temporal order

Use:

$$
h,\qquad h/2,\qquad h/4.
$$

### B. Establish fixed-step reference solutions

Progressively refine fixed timesteps until the solution trajectory is numerically stable.

### C. Demonstrate tolerance convergence

Tightening $A_j$ and $R_j$ should systematically reduce discrepancy from the reference solution.

### D. Test representative chemistry

At minimum:

- fast initial CO$_2$ acidification;
- calcite dissolution;
- silicate dissolution;
- precipitation from zero;
- mineral exhaustion;
- near-equilibrium kinetics;
- saturation-index crossing.

### E. Test real rollback

Verify rollback and retry with actual Reaktoro states after:

- temporal-error rejection;
- nonlinear solve failure;
- event correction.

### F. Validate event behaviour

Check:

- hard-event localisation;
- soft-event timestep capping;
- controller-history reset after hard events.

### G. Benchmark matched accuracy

Compare wall time at approximately equivalent numerical error.

Only after these tests should the scheme be treated as production-ready.

---

## 20. Final Architecture in One Line

> [!success]
> **Use Reaktoro's native kinetic solve as the chemical engine; estimate external temporal error by comparing one full step with two half steps; control that error primarily through reaction progress or kinetic mineral amounts; select the next timestep using an error-based I-controller; and impose separate hard/soft geochemical event caps, adding PI smoothing only after the core method is validated.**

---

# References

1. **Belfort, B., Carrayrou, J. & Lehmann, F. (2007).** *Implementation of Richardson extrapolation in an efficient adaptive time stepping method: Applications to reactive transport and unsaturated flow in porous media.* Transport in Porous Media, 69, 123–138. DOI: `10.1007/s11242-006-9090-3`

2. **Kavetski, D., Binning, P. & Sloan, S. W. (2002).** *Adaptive backward Euler time stepping with truncation error control for numerical modelling of unsaturated fluid flow.* International Journal for Numerical Methods in Engineering, 53, 1301–1322. DOI: `10.1002/nme.329`

3. **Söderlind, G. (2002).** *Automatic Control and Adaptive Time-Stepping.* Numerical Algorithms, 31, 281–310. DOI: `10.1023/A:1021160023092`

4. **Söderlind, G. (2003).** *Digital Filters in Adaptive Time-Stepping.* ACM Transactions on Mathematical Software, 29(1), 1–26. DOI: `10.1145/641876.641877`

5. **Younes, A. & Ackerer, P. (2010).** *Empirical versus time stepping with embedded error control for density-driven flow in porous media.* Water Resources Research, 46, W08523. DOI: `10.1029/2009WR008229`

6. **Leal, A. M. M., Blunt, M. J. & LaForce, T. C. (2015).** *A chemical kinetics algorithm for geochemical modelling.* Applied Geochemistry, 55, 46–61.

7. **Han, Z., Ren, G. & Younis, R. M. (2023).** *Automatic Time Step Control to Resolve Hydromechanically Driven Fault Reactivation, Spontaneous Nucleation, and Seismic Arrest.* Water Resources Research, 59, e2023WR035626. DOI: `10.1029/2023WR035626`

8. **Sæternes, E. H. & Cai, X. (2026).** *A control-theoretic approach to adaptive time stepping in reservoir simulations and its impacts on efficiency and robustness.* Communications in Nonlinear Science and Numerical Simulation, 158, 109828. DOI: `10.1016/j.cnsns.2026.109828`

9. **Reaktoro source code — KineticsSolver / KineticsUtils.** Current Reaktoro kinetic formulation and first-step preconditioning behaviour.
