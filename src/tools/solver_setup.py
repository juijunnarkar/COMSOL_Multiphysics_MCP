"""Physics-aware solver configuration tools for reactor CFD models.

Provides intelligent solver setup for FCCVD reactor simulations:
- Segregated solver matching the original Shelly model (4 steps)
- FullyCoupled solver with CFL pseudo-time stepping
- Parametric sweep for composition studies
- Convergence monitoring and diagnostics

Solver configurations are tuned for the multi-physics coupling in
FCCVD reactors: laminar flow + heat transfer + species transport +
surface-to-surface radiation.
"""

import time
from typing import Optional, List
from mcp.server.fastmcp import FastMCP

from .session import session_manager


def register_solver_setup_tools(mcp: FastMCP) -> None:
    """Register solver configuration tools."""

    # ====================================================================
    # Tool 1: solver_segregated_reactor
    # ====================================================================

    @mcp.tool()
    def solver_segregated_reactor(
        study_tag: Optional[str] = None,
        sol_tag: Optional[str] = None,
        study_type: str = "Stationary",
        max_iterations: int = 200,
        tolerance: float = 0.001,
        damping_factor: float = 1.0,
        min_damping: float = 1e-4,
        tracer_vars: Optional[List[str]] = None,
        flow_heat_vars: Optional[List[str]] = None,
        wall_fn_vars: Optional[List[str]] = None,
        radiation_vars: Optional[List[str]] = None,
        model_name: Optional[str] = None,
    ) -> dict:
        """
        Create a segregated solver configured for reactor multi-physics.

        Mirrors the original Shelly model's solver structure with 4 segregated
        steps that solve physics groups sequentially:
          Step 1: Tracer transport (species concentration)
          Step 2: Flow + Heat (velocity, pressure, temperature, wall temps)
          Step 3: Wall functions (uPlus)
          Step 4: Surface-to-surface radiation (band fluxes)

        This is the recommended solver for the full Shelly model with all
        physics active. It is more robust than FullyCoupled for strongly
        coupled multi-physics problems.

        Args:
            study_tag: Study tag (default: auto-generated "std_seg")
            sol_tag: Solution tag (default: auto-generated "sol_seg")
            study_type: "Stationary" or "Transient" (default: "Stationary")
            max_iterations: Maximum segregated iterations (default: 200)
            tolerance: Relative tolerance (default: 0.001)
            damping_factor: Initial damping factor (default: 1.0)
            min_damping: Minimum damping factor (default: 1e-4)
            tracer_vars: Variables for step 1 (default: ["comp1_tracer"])
            flow_heat_vars: Variables for step 2 (default: auto-detect)
            wall_fn_vars: Variables for step 3 (default: ["comp1_uPlus"])
            radiation_vars: Variables for step 4 (default: auto-detect)
            model_name: Model name (default: current model)

        Returns:
            Solver configuration details
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }

        try:
            jm = model.java

            study_tag = study_tag or "std_seg"
            sol_tag = sol_tag or "sol_seg"

            # Remove existing
            _remove_if_exists(jm.study(), study_tag)
            _remove_if_exists(jm.sol(), sol_tag)

            # Create study
            jm.study().create(study_tag)
            study = jm.study(study_tag)
            study.label("Segregated Reactor Study")

            step_type = "stat" if study_type == "Stationary" else "time"
            study.create(step_type, study_type)

            # Create solution sequence
            jm.sol().create(sol_tag)
            sol = jm.sol(sol_tag)
            sol.study(study_tag)
            # Without attach, the study runs its own default solver and ignores this one.
            sol.attach(study_tag)
            sol.label("Segregated Reactor Solver")

            # Study step
            sol.create("st1", "StudyStep")
            sol.feature("st1").set("study", study_tag)
            sol.feature("st1").set("studystep", step_type)

            # Variables
            sol.create("v1", "Variables")
            sol.feature("v1").set("control", step_type)

            # Stationary solver with Segregated approach
            sol.create("s1", "Stationary")
            s1 = sol.feature("s1")
            s1.set("control", step_type)
            s1.set("stol", str(tolerance))

            # Remove default FullyCoupled, add Segregated
            try:
                s1.feature().remove("fcDef")
            except Exception:
                pass

            s1.create("seg1", "Segregated")
            seg = s1.feature("seg1")
            seg.set("maxsegiter", str(max_iterations))
            seg.set("segstatefun", "mod")
            seg.set("segterm", "tol")
            seg.set("segtol", str(tolerance))

            # Default variable groups
            if tracer_vars is None:
                tracer_vars = ["comp1_tracer"]
            if flow_heat_vars is None:
                flow_heat_vars = [
                    "comp1_p", "comp1_u",
                    "comp1_T",
                    "comp1_nitf1_TWall_d", "comp1_nitf1_TWall_u",
                ]
            if wall_fn_vars is None:
                wall_fn_vars = ["comp1_uPlus"]
            if radiation_vars is None:
                radiation_vars = ["Ju_band", "Jd_band"]

            steps_config = [
                ("ss1", "Tracer", tracer_vars),
                ("ss2", "Flow + Heat", flow_heat_vars),
                ("ss3", "Wall Functions", wall_fn_vars),
                ("ss4", "Radiation", radiation_vars),
            ]

            created_steps = []
            for step_tag, step_label, step_vars in steps_config:
                seg.create(step_tag, "SegregatedStep")
                ss = seg.feature(step_tag)
                ss.label(step_label)
                ss.set("segvar", step_vars)

                # Configure per-step solver settings
                ss.set("subdamp", str(damping_factor))
                ss.set("subntolfact", "1")

                created_steps.append({
                    "tag": step_tag,
                    "label": step_label,
                    "variables": step_vars,
                })

            # Lower limit for damping
            try:
                seg.set("segmindamp", str(min_damping))
            except Exception:
                pass

            return {
                "success": True,
                "study_tag": study_tag,
                "sol_tag": sol_tag,
                "study_type": study_type,
                "solver_type": "Segregated",
                "max_iterations": max_iterations,
                "tolerance": tolerance,
                "steps": created_steps,
                "message": f"Segregated solver with {len(created_steps)} steps",
            }

        except Exception as e:
            return {"success": False, "error": f"Failed to create segregated solver: {str(e)}"}

    # ====================================================================
    # Tool 2: solver_fully_coupled
    # ====================================================================

    @mcp.tool()
    def solver_fully_coupled(
        study_tag: Optional[str] = None,
        sol_tag: Optional[str] = None,
        study_type: str = "Stationary",
        max_iterations: int = 200,
        tolerance: float = 0.001,
        min_damping: float = 1e-6,
        damping_technique: str = "auto",
        cfl_init: float = 1.0,
        use_cfl: bool = True,
        adaptive_cfl: bool = True,
        model_name: Optional[str] = None,
    ) -> dict:
        """
        Create a FullyCoupled solver with CFL pseudo-time stepping.

        This solver solves all physics simultaneously. Best for:
        - Reduced physics problems (flow + heat only, no radiation)
        - Analytic material properties (fewer coupling terms)
        - When convergence is difficult with segregated approach

        Uses CFL-based pseudo-time stepping for robust convergence
        from poor initial guesses.

        Args:
            study_tag: Study tag (default: "std_fc")
            sol_tag: Solution tag (default: "sol_fc")
            study_type: "Stationary" or "Transient"
            max_iterations: Maximum Newton iterations (default: 200)
            tolerance: Relative tolerance (default: 0.001)
            min_damping: Minimum damping factor (default: 1e-6)
            damping_technique: "auto", "const", or "hnlin" (default: "auto")
            cfl_init: Initial CFL number (default: 1.0, conservative)
            use_cfl: Enable CFL pseudo-time stepping (default: True)
            adaptive_cfl: Adaptive CFL tolerance (default: True)
            model_name: Model name (default: current model)

        Returns:
            Solver configuration details
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }

        try:
            jm = model.java

            study_tag = study_tag or "std_fc"
            sol_tag = sol_tag or "sol_fc"

            _remove_if_exists(jm.study(), study_tag)
            _remove_if_exists(jm.sol(), sol_tag)

            # Create study
            jm.study().create(study_tag)
            study = jm.study(study_tag)
            study.label("FullyCoupled Study")

            step_type = "stat" if study_type == "Stationary" else "time"
            study.create(step_type, study_type)

            # Create solver
            jm.sol().create(sol_tag)
            sol = jm.sol(sol_tag)
            sol.study(study_tag)
            # Without attach, the study runs its own default solver and ignores this one.
            sol.attach(study_tag)
            sol.label("FullyCoupled Solver")

            sol.create("st1", "StudyStep")
            sol.feature("st1").set("study", study_tag)
            sol.feature("st1").set("studystep", step_type)

            sol.create("v1", "Variables")
            sol.feature("v1").set("control", step_type)

            sol.create("s1", "Stationary")
            s1 = sol.feature("s1")
            s1.set("control", step_type)
            s1.set("stol", str(tolerance))

            # Configure FullyCoupled
            fc = s1.feature("fcDef")
            fc.set("maxiter", str(max_iterations))
            fc.set("mindamp", str(min_damping))
            fc.set("dtech", damping_technique)

            if use_cfl:
                fc.set("initcfl", str(cfl_init))
                fc.set("forcecfl", "on")
                if adaptive_cfl:
                    fc.set("adaptcfltol", "on")

            return {
                "success": True,
                "study_tag": study_tag,
                "sol_tag": sol_tag,
                "study_type": study_type,
                "solver_type": "FullyCoupled",
                "max_iterations": max_iterations,
                "tolerance": tolerance,
                "min_damping": min_damping,
                "cfl": {
                    "enabled": use_cfl,
                    "initial": cfl_init,
                    "adaptive": adaptive_cfl,
                },
                "message": f"FullyCoupled solver with CFL={cfl_init}, maxiter={max_iterations}",
            }

        except Exception as e:
            return {"success": False, "error": f"Failed to create FullyCoupled solver: {str(e)}"}

    # ====================================================================
    # Tool 3: solver_parametric_sweep
    # ====================================================================

    @mcp.tool()
    def solver_parametric_sweep(
        base_study_tag: str = "std_fc",
        base_sol_tag: str = "sol_fc",
        sweep_param: str = "x_he",
        sweep_values: Optional[List[float]] = None,
        sweep_range: Optional[str] = None,
        continuation: bool = True,
        model_name: Optional[str] = None,
    ) -> dict:
        """
        Add a parametric sweep to an existing study/solver.

        Wraps an existing solver in a parametric sweep that varies
        a global parameter. Useful for composition sweeps where
        x_he is varied from 0 to 1.

        Supports continuation (using previous solution as initial guess
        for next parameter value) for faster convergence.

        Args:
            base_study_tag: Existing study to sweep over
            base_sol_tag: Existing solver to wrap
            sweep_param: Parameter name to sweep (default: "x_he")
            sweep_values: List of parameter values (e.g., [0.0, 0.25, 0.5, 0.75, 1.0])
            sweep_range: COMSOL range expression (e.g., "range(0,0.1,1)")
                         Used if sweep_values is not provided
            continuation: Use previous solution as initial guess (default: True)
            model_name: Model name (default: current model)

        Returns:
            Parametric sweep configuration
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }

        try:
            jm = model.java

            # Verify base solver exists
            sol_tags = [str(t) for t in jm.sol().tags()]
            if base_sol_tag not in sol_tags:
                return {
                    "success": False,
                    "error": f"Base solver '{base_sol_tag}' not found. "
                             f"Create it first with solver_fully_coupled or solver_segregated_reactor."
                }

            sol = jm.sol(base_sol_tag)

            # Add parametric feature
            param_tag = "p1"
            try:
                sol.feature().remove(param_tag)
            except Exception:
                pass

            sol.create(param_tag, "Parametric")
            param = sol.feature(param_tag)
            param.set("pname", [sweep_param])

            if sweep_values is not None:
                # Convert to string list
                val_strs = [str(v) for v in sweep_values]
                param.set("plistarr", [" ".join(val_strs)])
                param.set("plist", " ".join(val_strs))
                n_values = len(sweep_values)
            elif sweep_range is not None:
                param.set("plistarr", [sweep_range])
                param.set("plist", sweep_range)
                n_values = sweep_range
            else:
                # Default: 5 compositions from 0 to 1
                default_vals = "0 0.25 0.5 0.75 1.0"
                param.set("plistarr", [default_vals])
                param.set("plist", default_vals)
                n_values = 5

            # Continuation
            if continuation:
                param.set("pcontinuationmode", "last")
            else:
                param.set("pcontinuationmode", "no")

            # Update study to enable parametric
            study = jm.study(base_study_tag)
            try:
                study.feature("stat").set("useadvanceddisable", True)
            except Exception:
                pass

            return {
                "success": True,
                "base_study": base_study_tag,
                "base_solver": base_sol_tag,
                "sweep_param": sweep_param,
                "n_values": n_values,
                "continuation": continuation,
                "message": f"Parametric sweep on '{sweep_param}' added to {base_sol_tag}",
            }

        except Exception as e:
            return {"success": False, "error": f"Failed to set up parametric sweep: {str(e)}"}

    # ====================================================================
    # Tool 4: solver_convergence_monitor
    # ====================================================================

    @mcp.tool()
    def solver_convergence_monitor(
        sol_tag: str = "sol_fc",
        log_file: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> dict:
        """
        Read convergence information from a solver.

        Extracts iteration history, residuals, and convergence status
        from a completed or in-progress solver run. Reads the solver's
        internal message log.

        Args:
            sol_tag: Solver tag to inspect (default: "sol_fc")
            log_file: Optional path to solver log file for additional info
            model_name: Model name (default: current model)

        Returns:
            Convergence data including iteration count, residuals, and status
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }

        try:
            jm = model.java

            sol_tags = [str(t) for t in jm.sol().tags()]
            if sol_tag not in sol_tags:
                return {
                    "success": False,
                    "error": f"Solver '{sol_tag}' not found. Available: {sol_tags}"
                }

            sol = jm.sol(sol_tag)

            # Extract solver messages
            messages = {}
            for feat_tag in ["st1", "s1", "v1"]:
                try:
                    feat = sol.feature(feat_tag)
                    msg = str(feat.getString("message"))
                    if msg and msg.strip():
                        messages[feat_tag] = msg
                except Exception:
                    pass

            # Parse iteration info from messages
            convergence_info = _parse_convergence(messages)

            # Read external log file if provided
            log_content = None
            if log_file:
                try:
                    with open(log_file, "r") as f:
                        log_content = f.read()[-5000:]  # Last 5000 chars
                except Exception:
                    pass

            return {
                "success": True,
                "sol_tag": sol_tag,
                "messages": messages,
                "convergence": convergence_info,
                "log_tail": log_content,
            }

        except Exception as e:
            return {"success": False, "error": f"Failed to read convergence: {str(e)}"}

    # ====================================================================
    # Tool 5: solver_diagnostics
    # ====================================================================

    @mcp.tool()
    def solver_diagnostics(
        sol_tag: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> dict:
        """
        Run diagnostics on solver configuration and results.

        Checks:
        - Solver structure (study, solver, features)
        - Whether a solution exists
        - Solution size and DOFs
        - Physics interfaces coupled to the study
        - Common configuration issues

        Args:
            sol_tag: Solver tag (default: checks all solvers)
            model_name: Model name (default: current model)

        Returns:
            Diagnostic report with configuration and recommendations
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }

        try:
            jm = model.java

            # List all studies
            study_tags = [str(t) for t in jm.study().tags()]
            sol_tags_all = [str(t) for t in jm.sol().tags()]

            studies = []
            for stag in study_tags:
                study = jm.study(stag)
                study_info = {
                    "tag": stag,
                    "label": str(study.label()),
                    "steps": [],
                }
                try:
                    for step_tag in [str(t) for t in study.feature().tags()]:
                        step = study.feature(step_tag)
                        study_info["steps"].append({
                            "tag": step_tag,
                            "type": str(step.getType()),
                        })
                except Exception:
                    pass
                studies.append(study_info)

            # Inspect solvers
            solvers = []
            inspect_tags = [sol_tag] if sol_tag else sol_tags_all

            for stag in inspect_tags:
                if stag not in sol_tags_all:
                    continue

                sol = jm.sol(stag)
                solver_info = {
                    "tag": stag,
                    "label": str(sol.label()),
                    "features": [],
                    "has_solution": False,
                }

                # Check for solution data
                try:
                    solver_info["has_solution"] = sol.isEmpty() is False
                except Exception:
                    pass

                # List features
                try:
                    for feat_tag in [str(t) for t in sol.feature().tags()]:
                        feat = sol.feature(feat_tag)
                        feat_info = {
                            "tag": feat_tag,
                            "type": str(feat.getType()),
                        }
                        # Check for sub-features (segregated steps, etc.)
                        try:
                            sub_tags = [str(t) for t in feat.feature().tags()]
                            if sub_tags:
                                sub_features = []
                                for sub_tag in sub_tags:
                                    sub = feat.feature(sub_tag)
                                    sub_info = {
                                        "tag": sub_tag,
                                        "type": str(sub.getType()),
                                    }
                                    try:
                                        sub_info["label"] = str(sub.label())
                                    except Exception:
                                        pass
                                    sub_features.append(sub_info)
                                feat_info["sub_features"] = sub_features
                        except Exception:
                            pass

                        solver_info["features"].append(feat_info)
                except Exception:
                    pass

                solvers.append(solver_info)

            # Recommendations
            recommendations = []
            if not sol_tags_all:
                recommendations.append(
                    "No solvers found. Use solver_segregated_reactor or "
                    "solver_fully_coupled to create one."
                )
            for s in solvers:
                if not s["has_solution"]:
                    recommendations.append(
                        f"Solver '{s['tag']}' has no solution. Run the study to solve."
                    )

            # Check datasets
            ds_tags = [str(t) for t in jm.result().dataset().tags()]

            return {
                "success": True,
                "studies": studies,
                "solvers": solvers,
                "datasets": ds_tags,
                "recommendations": recommendations,
            }

        except Exception as e:
            return {"success": False, "error": f"Failed to run diagnostics: {str(e)}"}


# ============================================================================
# Helper functions
# ============================================================================

def _remove_if_exists(container, tag: str):
    """Remove a node from a container if it exists."""
    try:
        tags = [str(t) for t in container.tags()]
        if tag in tags:
            container.remove(tag)
    except Exception:
        pass


def _parse_convergence(messages: dict) -> dict:
    """Parse convergence info from solver messages."""
    info = {
        "converged": None,
        "iterations": None,
        "final_residual": None,
    }

    # Check the stationary solver message (s1)
    s1_msg = messages.get("s1", "")
    if not s1_msg:
        return info

    lines = s1_msg.strip().split("\n")

    # Look for iteration count
    for line in lines:
        line_lower = line.lower()
        if "converged" in line_lower:
            info["converged"] = True
        elif "not converged" in line_lower or "failed" in line_lower:
            info["converged"] = False

        # Look for "Number of iterations: N"
        if "iteration" in line_lower:
            parts = line.split()
            for i, part in enumerate(parts):
                try:
                    n = int(part)
                    info["iterations"] = n
                    break
                except ValueError:
                    continue

    # Count data lines (SolEst lines from the iteration table)
    data_lines = [l for l in lines if l.strip() and not l.startswith("#")
                  and any(c.isdigit() for c in l)]
    if data_lines and info["iterations"] is None:
        info["iterations"] = len(data_lines)

    return info
