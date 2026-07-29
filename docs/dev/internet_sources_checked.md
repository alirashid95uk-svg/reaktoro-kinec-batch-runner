# Official Internet Sources Checked

Checked on 2026-06-13. Only official OpenAI Codex and Reaktoro documentation
was used. No blogs, third-party projects, or unrelated examples were used.

## OpenAI Codex Documentation

| Source title | URL | What was learned | Project effect |
|---|---|---|---|
| Custom instructions with AGENTS.md | https://developers.openai.com/codex/guides/agents-md | Codex reads project `AGENTS.md` guidance before work and supports layered repository instructions. | Root `AGENTS.md` holds durable project-wide rules. |
| Agent Skills | https://developers.openai.com/codex/skills | A skill is a directory with `SKILL.md`; `SKILL.md` requires `name` and `description` frontmatter; repo skills belong under `.agents/skills`. | The five repo skills use focused directories and valid frontmatter. |
| Customization | https://developers.openai.com/codex/concepts/customization | `AGENTS.md` is for persistent project guidance, while skills package reusable workflows and domain expertise. | Durable rules stay in `AGENTS.md`; focused enforcement workflows stay in skills. |

## Reaktoro Documentation

| Source title | URL | What was learned | Project effect |
|---|---|---|---|
| Loading thermodynamic databases | https://reaktoro.org/tutorials/basics/loading-databases.html | Embedded PHREEQC databases can be loaded by explicit name and local PHREEQC files with `PhreeqcDatabase.fromFile`. | Database config supports only explicit embedded or local PHREEQC sources. |
| PhreeqcDatabase class reference | https://reaktoro.org/api/classReaktoro_1_1PhreeqcDatabase.html | `PhreeqcDatabase` represents PHREEQC databases; invalid embedded names and invalid local files raise errors. | The project validates config and fails loudly without fallback. |
| Defining chemical systems | https://reaktoro.org/tutorials/basics/defining-chemical-systems.html | Reaktoro constructs systems directly from configured phases using `ChemicalSystem`. | Developer guidance keeps phase and system construction visible. |
| AqueousPhase class reference | https://reaktoro.org/api/classReaktoro_1_1AqueousPhase.html | `AqueousPhase` configures an aqueous solution phase and accepts explicit species or element-based selection. | The project uses direct `AqueousPhase` construction. |
| GaseousPhase class reference | https://reaktoro.org/api/classReaktoro_1_1GaseousPhase.html | `GaseousPhase` configures a gaseous solution phase and supports an explicit activity model. | A gas phase is created only when gas is enabled. |
| MineralPhases class reference | https://reaktoro.org/api/classReaktoro_1_1MineralPhases.html | `MineralPhases` configures pure mineral phases from explicit names or selected elements. | Mineral phase construction remains direct and readable. |
| ChemicalSystem class reference | https://reaktoro.org/api/classReaktoro_1_1ChemicalSystem.html | `ChemicalSystem` can include a database, phases, reactions, and surfaces. | Kinetic system construction must explicitly include configured reactions and surfaces. |
| Creating chemical states | https://reaktoro.org/tutorials/basics/creating-chemical-states.html | `ChemicalState` stores initial/computed states and supports direct temperature, pressure, and species amount setting with units. | State construction uses explicit config values and units. |
| ChemicalState class reference | https://reaktoro.org/api/classReaktoro_1_1ChemicalState.html | `ChemicalState` exposes direct setters and species-amount/property access. | Initial conditions and standard amount outputs remain direct. |
| Specifying activity models | https://reaktoro.org/tutorials/basics/specifying-activity-models.html | `ActivityModelPhreeqc` mirrors PHREEQC aqueous activity behavior; Peng-Robinson and PHREEQC-compatible Peng-Robinson variants are supported for fluids. | PHREEQC aqueous systems default by project rule to `ActivityModelPhreeqc`; gas model selection is explicit. |
| API reference: activity models | https://reaktoro.org/api/ | The API lists `ActivityModelPengRobinson`, `ActivityModelPengRobinsonPhreeqc`, and other activity-model choices. | Documentation names official gas-model options while requiring an explicit project choice. |
| MineralReaction class reference | https://reaktoro.org/api/classReaktoro_1_1MineralReaction.html | `MineralReaction` configures mineral dissolution/precipitation reactions and accepts rate models. | The supplied Kinec adapter attaches directly to `MineralReaction`. |
| MineralSurface class reference | https://reaktoro.org/api/classReaktoro_1_1MineralSurface.html | `MineralSurface` defines reacting mineral surfaces and requires an area value with compatible units for explicit area models. | Every kinetic mineral requires an explicit surface area and unit. |
| Core API: ReactionRateModel | https://reaktoro.org/api/group__Core.html | `ReactionRateModel` is an official rate function type evaluated from `ChemicalProps`; Reaktoro also defines mineral-specific rate-model interfaces. | `ReactionRateModelKinec` is documented as a project adapter whose returned official type, sign, and units require validation. |
| KineticsSolver class reference | https://reaktoro.org/api/classReaktoro_1_1KineticsSolver.html | `KineticsSolver` preconditions states and reacts them over a time interval in seconds. | Kinetic execution uses explicit solver calls and time units. |
| Reaktoro namespace reference: equilibrate | https://reaktoro.org/api/namespaceReaktoro.html | `equilibrate(state)` performs a closed equilibrium calculation at the state's temperature and pressure. | The minimal equilibrium pattern uses the direct official function. |
| Computing aqueous properties | https://reaktoro.org/tutorials/basics/computing-aqueous-properties.html | `AqueousProps(state)` exposes aqueous properties after state construction/calculation. | Standard outputs include pH, pE, and saturation properties through `AqueousProps`. |
| AqueousProps class reference | https://reaktoro.org/api/classReaktoro_1_1AqueousProps.html | `AqueousProps` exposes pH, pE, alkalinity, species molalities, and saturation properties. | Postprocessing uses `AqueousProps` for aqueous-specific diagnostics. |
| ChemicalProps class reference | https://reaktoro.org/api/classReaktoro_1_1ChemicalProps.html | `ChemicalProps` computes general system, phase, and species properties. | Rate evaluation and general diagnostics use explicit `ChemicalProps`. |

## Verification Status

Official sources were checked sufficiently to guide the project.

Exact Python syntax must still be tested locally during implementation.

`ReactionRateModelKinec` was not found as an official Reaktoro symbol because
it is the user-supplied Kinec YAML -> Reaktoro kinetic-rate adapter.
