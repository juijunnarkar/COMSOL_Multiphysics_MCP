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

The server registers 89 MCP tools. `tests/test_readme_tools.py` checks
that this table lists every registered tool and no other tool. Each tool's
docstring gives its parameters.

| Group | Tools |
|-------|-------|
| Session | `comsol_start`, `comsol_connect`, `comsol_disconnect`, `comsol_status` |
| Model | `model_introspect`, `model_load`, `model_create`, `model_create_component`, `model_list_components`, `model_save`, `model_save_version`, `model_list`, `model_set_current`, `model_clone`, `model_remove`, `model_inspect` |
| Parameters | `param_get`, `param_set`, `param_list`, `param_sweep_setup`, `param_description` |
| Geometry | `geometry_list`, `geometry_create`, `geometry_add_feature`, `geometry_add_block`, `geometry_add_cylinder`, `geometry_add_sphere`, `geometry_add_rectangle`, `geometry_add_circle`, `geometry_boolean_union`, `geometry_boolean_difference`, `geometry_import`, `geometry_build`, `geometry_list_features`, `geometry_get_boundaries` |
| Physics and materials | `physics_list`, `physics_get_available`, `physics_add`, `physics_add_electrostatics`, `physics_add_solid_mechanics`, `physics_add_heat_transfer`, `physics_add_laminar_flow`, `physics_configure_boundary`, `physics_set_material`, `multiphysics_add`, `physics_list_features`, `physics_remove`, `physics_interactive_setup_flow`, `physics_setup_flow_boundaries`, `physics_interactive_setup_heat`, `physics_setup_heat_boundaries`, `physics_boundary_selection` |
| Mesh | `mesh_list`, `mesh_create`, `mesh_info`, `mesh_quality_report`, `mesh_refine_local`, `mesh_boundary_layer` |
| Reactor mesh (FCCVD defaults) | `mesh_reactor_2d`, `mesh_reactor_3d` |
| Study | `study_list`, `study_solve`, `study_solve_async`, `study_get_progress`, `study_cancel`, `study_wait`, `solutions_list`, `datasets_list` |
| Solver setup | `solver_segregated_reactor`, `solver_fully_coupled`, `solver_parametric_sweep`, `solver_convergence_monitor`, `solver_diagnostics` |
| Results | `results_evaluate`, `results_global_evaluate`, `results_inner_values`, `results_outer_values`, `results_export_data`, `results_export_image`, `results_exports_list`, `results_plots_list` |
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
$COMSOL_BIN mphserver -port 2036 -login auto

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
