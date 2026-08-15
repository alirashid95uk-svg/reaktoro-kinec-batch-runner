# Workbench acceptance evidence

Evidence was collected through 11 August 2026 on Windows in the repository root.
Generated acceptance artifacts are under the ignored `.workbench/acceptance/`
directory; generated simulation runs remain under ignored `runs/`.

## Verified environments

The solver environment `fypr-reaktoro` contained Python 3.11.15, Reaktoro
2.13.0, Pydantic 2.13.4, PyYAML 6.0.3, Matplotlib 3.10.9, and pytest 9.0.3.
No package was installed into it during this upgrade. PySide6 6.11.0 was
already present before this work; pyqtgraph and ruamel.yaml remain absent.

The separately created `reaktoro-workbench` environment contained Python
3.11.15, PySide6 6.11.1, pyqtgraph 0.14.0, ruamel.yaml 0.19.1, pandas 3.0.5,
pyarrow 25.0.0, Pydantic 2.13.4, PyYAML 6.0.3, Markdown 3.10.3, ReportLab
5.0.0, Pillow 12.3.0, pytest 9.1.1, pytest-qt 4.5.0, and pywinauto 0.6.9.
Reaktoro was absent. The exact pins are in `environment-workbench.yml`.

No dependency was added for the UX redesign. The presentation layer uses Qt
Fusion, Segoe UI from Windows when available, standard Qt icons, and two small
local helper modules for cards, action bars, disclosures, and empty states.

## UX redesign and responsive evidence

- The retained evidence manifest contains 84 native Qt captures: all seven
  workspaces, empty and representative states, 1024 x 600 and 1440 x 900
  logical sizes, and `QT_SCALE_FACTOR` 1.00, 1.25, and 1.50. Every record
  includes the screenshot path/hash, focus target, device-pixel ratio,
  root-scroll result, and automated failures. The final manifest reports zero
  automated failures.
- Manual visual inspection sampled all seven representative workspaces at
  1024 x 600, the critical empty and populated states at 150%, and a populated
  1440 x 900 view. It corrected disclosure clipping, tab overflow, empty-state
  text clipping, sidebar truncation, and weak evidence-pane sizing. The full
  84-capture set is retained for review; this document does not falsely claim
  that every image received an independent human inspection.
- Empty Explore and Compare states display white explanatory panels instead of
  plots. Loaded Explore plots use a white background and preserve the exact
  selected labels `Time (seconds)` and `Time (days)`. Comparison series retain
  distinct line styles and symbols as well as colour.
- Native Windows UI Automation passed five tests covering launcher splash
  ordering, Ctrl+1 through Ctrl+7 navigation across all workspaces, sampled
  visible/enabled focus traversal and accessible identifiers/roles/HelpText,
  and Runs-to-Explore table navigation. Complete workflow coverage is provided
  by Qt state tests, not claimed from UIA alone. Those state tests cover empty,
  dirty, clean, validated, stale, blocked, running, paused, completed, and
  failed views.
- The splash is constructed before `MainWindow` import/construction and was
  observed before the main window through the real CMD launcher. The launcher
  explains that the GUI opens separately, keeps the console open during the
  session, and retains actionable failures.

The machine-readable manifest is `.workbench/acceptance/ux/manifest.json`; its
HTML contact sheet is `.workbench/acceptance/ux/contact-sheet.html`. These are
ignored generated artifacts rather than source-controlled fixtures.

Windows Graphics Capture could uniquely identify the PySide process but could
not capture its surface (`foreground window did not report a process id`).
Therefore physical compositor/multi-monitor DPI coverage is not claimed; the
documented evidence is native Windows Qt rendering plus the automated logical-
size suite.

## Automated and scale verification

The representative scale suite demonstrated:

| Fixture | Result | Measured target-workstation wall time |
|---|---:|---:|
| 10,000 mixed-state artifact run records, rebuilt twice and queried by state | deterministic 10,000; 2,500 per state | included below |
| 1,000,000 timeseries rows | streamed in ten 100,000-row chunks | included below |
| 250,000 solver-attempt rows | streamed in five 50,000-row chunks | included below |
| 500 traceable study samples | strict manifest accepted | included below |

The three combined scale tests exited successfully in 266.86 s
whole-command wall time. This includes creating and cleaning 10,000 real
Windows directories; no arbitrary responsiveness threshold is claimed.

## Real process and scientific evidence

- The final direct PySide6 `QProcess` smoke run
  `785f7edf-296f-4033-b611-e16b2d385cf5` used the unchanged verified solver
  environment, reached its requested 259,200 s state, and produced a complete
  `objective1_audit_v4` package. The authoritative Objective 1 auditor returned
  `ok: true`, no errors or warnings, 73 timeseries rows, and 73 solver-history
  rows.
- A managed custom-Kinec smoke run
  `dd6606ef-cb45-429d-a1e8-a388fc9e0d06` completed with 81 of 81 events
  parseable. Its Windows finalisation path returned only after durable output.
- A previously interrupted native run was recovered conservatively as
  `interrupted_by_host`; no stale lock or success inference remained.

The final QProcess run covers current controller-process metadata, package
completion evidence, and the saved-artifact audit. A clean package audit is
not evidence of calibration, timestep convergence, reactive transport, or
fracture sealing.

## Replacement equivalence

The workbench run `257c763a-0193-43bc-a9bd-2fb47fe90534`, preserved legacy
launcher result, and direct CLI result used the same source-supported
Calcite/Quartz/Illite fixed-fugacity case. Full preflight passed with all three
mineral mappings active. After removing approved operational path/identifier
fields, all resolved scientific payloads had SHA-256
`fededc80c2d7666c29c0c7b2dede8511d0112dfe6cb71730b48abde93dea1864`.

The three paths produced identical SHA-256 values:

| Artifact | SHA-256 |
|---|---|
| `timeseries.csv` | `f54ac1f3299a48513a760ff5e6aeacd5a80c32318e6a56860401f9cef6cd8124` |
| `mineral_summary.csv` | `69113bed08701b900f5f7e4ed87118e018a8a9f21e83ad01ecf6dc7b3b6c7cd4` |
| `aqueous_summary.csv` | `14a545a6f8a54c1ef0335d1147517b5eb2096312a0e542c458bd46ca6fbb13a9` |

All diagnostics classified completion consistently and used output schema v4.
This evidence permits the workbench bootstrap to become the documented
default while retaining the legacy launcher.

## Derived-artifact evidence

A two-run native-grid pH comparison was exported and reproduced through
`compare-reproduce`; both CSVs had SHA-256
`67e1aca2b78b971fbe203048d272408f9d6e548e26ca52fb9e4c9de53575683e`.
A final-state dataset with explicit pH feature, Calcite-amount target,
replicate policy, scenario-group rule, seed, and split proportions passed the
authoritative package auditor and wrote CSV, Parquet, failure ledger, and
manifest hashes. Dataset and comparison report sets were generated through the
CLI. Their final PDFs were rendered and visually checked for clipping,
overlap, legibility, table alignment, source hashes, scope warning, and page
numbering. Report reproduction resolves source files by logical name and
SHA-256; Markdown and HTML reproduce byte-for-byte. ReportLab PDF output is
regenerated headlessly from the same recorded specification and sources, but
binary byte identity is not claimed.
All comparison, dataset, and report writers reject output paths equal to or
nested below immutable source evidence directories before creating anything;
the same guard applies to comparison and report reproduction.

## Independent review loops

Separate reviews covered core scientific/provenance boundaries, Qt/process
architecture, Windows process-tree behaviour, YAML integrity, queue recovery,
comparison/study/dataset safety, accessibility, tests, and specification
coverage. Review findings that exposed stale locking, nested process control,
missing force evidence, false external-save conflicts, stale queue recovery,
shallow artifact views, non-transactional template edits, mouse-only value
editing, and shifted list removals were corrected before the final frozen-tree
audit. Final independent scientific/data and GUI/process reviews found no
remaining blocker.

## Declared limitations and exact-spec exceptions

- Only `objective1_audit_v4` is scientifically supported. The output contract
  explicitly rejects older packages because thermodynamic/kinetic provenance
  and mineral-name contracts changed. Therefore the requested benchmark of
  multiple *supported* output-schema versions cannot be implemented honestly;
  unsupported versions remain visible as raw artifacts and are refused for
  interpretation.
- Every v4 quantity currently declares interpolation forbidden because no
  scientifically approved variable-class interpolation policy exists.
  Explicit interpolation machinery checks policy and extrapolation, but no
  current comparison or dataset silently enables it.
- Parallel solver execution remains disabled at one worker. The specification
  permits enabling it only after verified Windows concurrency and resource
  tests; those scientific/runtime tests do not yet exist.
- A graceful cancellation cannot interrupt an active native Reaktoro call.
  The controller records the unresponsive-cancel state and supports explicit
  Windows process-tree force termination.
- Existing unmanaged legacy output packages cannot retroactively provide run
  records, validation receipts, or source fingerprints that were never saved.
  They remain inspectable but are excluded from managed comparisons/datasets.
- Qt uses native high-DPI scaling; automated logical-size and native Qt factor
  renders cover 100%, 125%, and 150%. Windows Graphics Capture could not bind
  the PySide surface, so no physical compositor or multi-monitor DPI visual
  measurement is claimed.

No thermodynamic database, kinetic parameter value, scientific case value,
Reaktoro equation, solver default, timestep-acceptance threshold, or scientific
output definition was intentionally changed by the GUI migration.

## Final frozen-tree command matrix

| Verification | Result |
|---|---|
| `conda run -n fypr-reaktoro python -m pytest -q` | 93 passed, 1 Windows-GUI module skipped, in 14.30 s |
| Workbench GUI and process-controller suite | 46 passed in 170.87 s |
| Workbench core, excluding scale | 24 passed in 2.96 s |
| Workbench scale acceptance | 3 passed in 266.86 s |
| Native Windows UI Automation | 5 passed in 59.20 s |
| Visual evidence manifest | 84 records; zero automated failures |
| Legacy launcher focused suite | 3 passed in 0.38 s |
| Solver and workbench `compileall` | Pass |
| Environment Doctor | Solver ready; workbench ready; separation verified |
| Qt import scan of `batch_runner` and `workbench_core` | Clean |
| Protected scientific-file SHA-256 recheck | Exact baseline match |
| `git diff --check` | Clean; line-ending conversion warnings only |
| Final real PySide6 `QProcess` run | `785f7edf...`; completed and output complete in 197.5 s |
| Objective 1 audit of final result package | `ok: true`; no errors or warnings |

The final solver run occurred after the UX redesign and exercised the unchanged
`QProcess` controller and scientific runner used by the Queue workspace.
The protected scientific-file hash audit and relevant automated suites were
then repeated on the same tree.
