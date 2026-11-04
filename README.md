# COMSOL Multiphysics MCP Server

**Physics-aware MCP tooling for COMSOL via Java API**

![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)
![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.19503007.svg)](https://doi.org/10.5281/zenodo.19503007)
[![tests](https://github.com/juijunnarkar/COMSOL_Multiphysics_MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/juijunnarkar/COMSOL_Multiphysics_MCP/actions/workflows/tests.yml)

## What This Is

The first MCP server for COMSOL Multiphysics with physics-aware tooling.
Enables Claude (or any MCP client) to drive COMSOL programmatically: build
meshes, configure solvers, run parametric sweeps, and extract results with
zero GUI interaction required.

The server communicates with a running COMSOL `mphserver` process through the
[MPh](https://mph.readthedocs.io/) Python library, which wraps the COMSOL
Java API via JPype.

## Built On

Forked from [wjc9011/COMSOL_Multiphysics_MCP](https://github.com/wjc9011/COMSOL_Multiphysics_MCP) (MIT License).
That project provided the foundational MCP-to-COMSOL Java API bridge. This
project adds physics-aware mesh generation, analytic thermodynamic property
expressions, segregated solver tooling with physics-aware step grouping,
workflow memory, 2D-to-3D conversion, and a simulation-in-the-loop Bayesian
optimization pipeline for CNT reactor design.

## Novel Contributions

- **Physics-aware mesh builder**: boundary detection via `getVertexCoord()`
  and `getAdj(1,0)`, injector refinement, boundary layers, quality reporting.
  No hardcoded geometry tags.
- **Analytic gas property bypass**: density (ideal gas), viscosity (Wilke
  mixing, 3.5% max error), thermal conductivity (Mason-Saxena blend, 2.41%
  max error), Cp (NASA polynomials, 0.12% max error). 51 COMSOL variable
  expressions, zero GUI clicks per composition change.
- **Segregated solver setup**: 4-step physics-aware grouping (radiosity,
  nonisothermal flow, turbulence, wall functions).
- **Workflow memory**: `memorize_workflows.py` stores successful solver
  configurations as Digital Twin JSON recipes for reuse.
- **2D-to-3D converter**: automated axisymmetric-to-3D geometry promotion
  with CylinderSelectionMapper for selection remapping.
- **Validator and autofixer**: detects and repairs common model errors
  (empty selections, material assignment, multiphysics dependency conflicts).
- **ARES-Sim bridge**: connects multi-objective Bayesian optimization output
  directly to COMSOL CFD validation (gRPC protocol).

## Architecture

```
                        MCP Protocol
Claude / MCP Client  ──────────────>  MCP Server (src/server.py)
                                            |
                                      Tools Layer
                                    (src/tools/*.py)
                                            |
                                      MPh 1.3.1
                                      (JPype bridge)
                                            |
                                   COMSOL mphserver
                                    (port 2036)
                                            |
                                      .mph model

ARES-Sim BO  ──>  comsol_bridge  ──>  MPh layer (same connection)
```

## Tool Reference

The server registers 108 MCP tools. `tests/test_readme_tools.py` checks
that this table lists every registered tool and no other tool. Each tool's
docstring gives its parameters.

| Group | Tools |
|-------|-------|
| Session | `comsol_start`, `comsol_connect`, `comsol_disconnect`, `comsol_status`, `comsol_progress_log` |
| Model | `model_introspect`, `model_load`, `model_create`, `model_create_component`, `model_list_components`, `model_save`, `model_save_version`, `model_list`, `model_set_current`, `model_clone`, `model_remove`, `model_inspect` |
| Parameters | `param_get`, `param_set`, `param_list`, `param_sweep_setup`, `param_description` |
| Geometry | `geometry_list`, `geometry_create`, `geometry_add_feature`, `geometry_add_block`, `geometry_add_cylinder`, `geometry_add_sphere`, `geometry_add_rectangle`, `geometry_add_circle`, `geometry_boolean_union`, `geometry_boolean_difference`, `geometry_import`, `geometry_build`, `geometry_list_features`, `geometry_get_boundaries`, `geometry_get_domains`, `geometry_get_edges` |
| Physics and materials | `physics_list`, `physics_get_available`, `physics_add`, `physics_add_electrostatics`, `physics_add_solid_mechanics`, `physics_add_heat_transfer`, `physics_add_laminar_flow`, `physics_configure_boundary`, `physics_configure_domain`, `physics_set_property`, `material_create`, `material_set_property`, `physics_set_material`, `multiphysics_add`, `physics_list_features`, `physics_remove`, `physics_interactive_setup_flow`, `physics_setup_flow_boundaries`, `physics_interactive_setup_heat`, `physics_setup_heat_boundaries`, `physics_boundary_selection` |
| Mesh | `mesh_list`, `mesh_create`, `mesh_add_feature`, `mesh_info`, `mesh_quality_report`, `mesh_refine_local`, `mesh_boundary_layer` |
| Reactor mesh (FCCVD defaults) | `mesh_reactor_2d`, `mesh_reactor_3d` |
| Study | `study_list`, `study_create`, `study_configure_step`, `study_configure_solver`, `study_transient_from_solution`, `study_use_solution`, `study_solve`, `study_solve_async`, `study_get_progress`, `study_cancel`, `study_wait`, `solutions_list`, `datasets_list` |
| Solver setup | `solver_segregated_reactor`, `solver_fully_coupled`, `solver_parametric_sweep`, `solver_convergence_monitor`, `solver_diagnostics` |
| Results | `results_evaluate`, `results_probe_time_series`, `results_stat`, `results_global_evaluate`, `results_inner_values`, `results_outer_values`, `results_export_data`, `results_export_image`, `results_exports_list`, `results_plots_list`, `results_dataset_add`, `results_plot_add`, `results_plot_image`, `results_plots_clear` |
| Documentation | `docs_get`, `docs_list`, `physics_get_guide`, `troubleshoot`, `modeling_best_practices`, `pdf_search`, `pdf_search_status`, `pdf_list_modules` |

### Workflow scripts (not MCP tools yet)

These scripts run from Python against an open model. An MCP client cannot
call them yet.

| Script | What it does |
|--------|--------------|
| `workflows/comsol_validator.py` | Checks a model for empty selections, material assignment, mesh, and multiphysics errors |
| `workflows/comsol_autofixer.py` | Repairs the errors that the validator finds |
| `workflows/memorize_workflows.py` | Captures the model structure as a Digital Twin JSON recipe |
| `workflows/convert_2D_to_3D.py` | Converts a 2D axisymmetric recipe to a 3D model |
| `workflows/Comsol_live_monitor.py` | Reports live model state: solver status, errors, warnings, physics, materials, and mesh quality |
| `analytic_properties/apply_analytic_properties.py` | Applies the analytic gas property expressions |

## Transient Studies

Use a Transient (time-dependent) study when a steady solve does not
converge. It shows whether the flow settles to one state or keeps moving.
Three tools hold the recipe:

1. `study_transient_from_solution` creates a Transient study that starts
   from the last state of a solved study. It sets the output times
   (`tlist`), the relative tolerance (`rtol`), and the physics to solve. The
   initial values, and the values of the physics that it does not solve,
   come from the solved study.
2. `results_probe_time_series` reads expressions at a list of points for
   every output time.
3. `study_use_solution` makes another study, for example a heat study, read
   the last state of the Transient study.

```python
call("study_transient_from_solution", study_tag="std3", from_study="std1",
     tlist="range(0,1e-3,0.06)", rtol="1e-3",
     solve_for={"/physics/ht": False, "/multiphysics/noni1": False})
call("study_solve", study_name="Study 3")  # MPh solves by label. The setup result gives it.
call("results_probe_time_series", points=[[0.02, 0.001, 0.0125]], expressions="u",
     study_tag="std3", names=["channel_mid"])
call("study_use_solution", study_tag="std2", from_study="std3")
call("study_solve", study_name="Study 2")
```

`examples/transient_from_steady.py` runs these steps on a saved model and
writes the probes to a CSV file.

### Solver options

The defaults of `study_transient_from_solution` come from one model on
COMSOL 6.4. That model is a laminar flow unit cell of a plate-fin heat sink,
with 193 000 degrees of freedom (DOFs). They are options, not fixed settings. Pass
`None` to keep the COMSOL default.

| Option | Default | Why |
|--------|---------|-----|
| `consistent` | `"off"` | With the consistent start step on, Newton did not converge at t = 1e-7 s, with both the direct and the iterative linear solver. With it off, the runs started. |
| `linear_solver` | `"direct"` | In steady runs of the same cell, the direct solver took 2 s per iteration on 32 cores, against 14 s for the iterative solver. It needs more memory. If it runs out of memory, use `"iterative"`. |
| `jacobian_update` | `"onevery"` | A new Jacobian at each Newton step. The runs that worked used it. Its effect alone was not tested. |

On the Rice NOTS cluster, with these settings, a 5 ms run took 4.5 minutes
(31 time steps). A 60 ms run took 36 minutes (69 time steps). On a
laptop, the same settings, from a different start state, ran 2 hours
without reaching 1 ms of flow time. The cause of that difference was not
found.

### Probes

MPh cannot evaluate on a Cut Point 3D dataset. It reports that the dataset
"does not refer to a solution". `results_probe_time_series` evaluates
`at3(x,y,z,expr)` (`at2` in 2D) on the solution dataset instead. Each term
is a global expression, so MPh returns one row per output time and one
column per term.

### Reuse the last state

The step settings of a study reach its solver sequence only when COMSOL
creates the sequence. An existing sequence keeps its own copy in its
Variables feature (`v1`). So `study_use_solution` sets the step and also
sets `notsol` of `v1` to the solution of the other study, with
`notsolnum` = `last`.

### Checks

1. The mock tests in `tests/test_transient_tools.py` cover the three tools.
   Their solver tree copies the tree that COMSOL 6.4 builds.
2. On 2026-11-03, the three tools and the example ran on COMSOL 6.4 on an
   arm64 Mac. The model was a small 3D channel with laminar flow and heat
   transfer.
   A 1 ms Transient flow run (11 output times) took about 70 s. COMSOL
   accepted every step and `v1` setting, and the heat study solved on the
   last Transient state.
3. A 5 ms run of the same channel on a finer mesh did not finish in
   18 minutes, so the check stopped it. The cause of the slow run was not
   found.

### Limits

1. The time step limits what a run can show. In the 60 ms run, every step
   from about 13 ms was 0.96 ms long. A step of that length cannot resolve
   vortex shedding near 1 kHz. Such shedding has a period near 1 ms.
2. The BDF time solver (backward differentiation formula) damps changes
   that are faster than its step. A run that looks steady can still hide
   fast shedding. To look for it, set a maximum step well below the
   shedding period with `study_configure_solver`, for example
   `{"Time": {"maxstepconstraintbdf": "const", "maxstepbdf": "1e-4"}}`.
   This setting was not tested on the heat sink cell.
3. Symmetry planes allow only symmetric motion. A model with symmetry
   planes cannot show shedding that breaks that symmetry.
4. Run `study_transient_from_solution` on a study with no solver sequence.
   COMSOL may not copy new step settings into an existing sequence. The
   tool returns a warning in that case.

## Installation

### Prerequisites

- COMSOL Multiphysics 6.3 or later
- Python 3.12 (ARM64-native on Apple Silicon)
- conda (Miniconda or Anaconda)

### Setup

```bash
# 1. Clone the repository
git clone https://github.com/sparkyScientist/COMSOL_Multiphysics_MCP.git
cd COMSOL_Multiphysics_MCP

# 2. Create conda environment
conda env create -f environment.yml
conda activate comsol-mcp

# 3. Configure
cp .env.example .env
# Edit .env with your COMSOL path and port

# 4. Start COMSOL mphserver (in a separate terminal)
# -multi on keeps the server running after a client disconnects.
$COMSOL_BIN mphserver -port 2036 -login auto -multi on

# 5. Verify connection
python -c "import mph; c=mph.Client(port=2036); print('Connected:', c.names())"
```

### MCP Client Configuration

Add to your MCP client settings (e.g., Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "comsol": {
      "command": "python",
      "args": ["src/server.py"],
      "cwd": "/path/to/COMSOL_Multiphysics_MCP"
    }
  }
}
```

## Quick Start

```python
import mph

# Connect to running mphserver
client = mph.Client(port=2036)

# Load a model
model = client.load("my_reactor.mph")

# The MCP tools handle the rest:
# - mesh_reactor_3d builds a physics-aware mesh
# - solver_segregated_reactor configures the solver
# - solver_parametric_sweep runs composition sweeps
# - Results are extracted automatically
```

## Validated On

Developed and validated on 2D and 3D FCCVD carbon nanotube reactor models
(vertical downflow, horizontal) with nonisothermal flow, k-epsilon
turbulence, surface-to-surface radiation, and multi-component He/N2/CH4/H2
gas mixtures. Analytic property accuracy validated against COMSOL
Thermodynamics at 5 gas compositions (0-61% He by mole).

## Citation

If you use this software in your research, please cite:

```bibtex
@software{junnarkar2026comsol_mcp,
  author    = {Junnarkar, Jui},
  title     = {COMSOL Multiphysics MCP Server},
  version   = {0.1.0},
  year      = {2026},
  url       = {https://github.com/sparkyScientist/COMSOL_Multiphysics_MCP},
  doi       = {10.5281/zenodo.19503007}
}
```

See [CITATION.cff](CITATION.cff) for machine-readable citation metadata.

## License

GPL-3.0. See [LICENSE](LICENSE).

Upstream portions from [wjc9011/COMSOL_Multiphysics_MCP](https://github.com/wjc9011/COMSOL_Multiphysics_MCP) retain their original MIT License attribution.
