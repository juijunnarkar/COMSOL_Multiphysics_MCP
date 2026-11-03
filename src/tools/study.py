"""Study and solving tools for COMSOL MCP Server."""

from typing import Optional
from mcp.server.fastmcp import FastMCP

from .session import session_manager
from ..async_handler.solver import async_solver


def solution_status(model) -> dict:
    """Return {solution tag: True if it holds computed data}, read from the Java model."""
    jm = model.java
    status = {}
    for tag in [str(t) for t in jm.sol().tags()]:
        try:
            status[tag] = not bool(jm.sol(tag).isEmpty())
        except Exception:
            status[tag] = False
    return status


def check_solved(model, study_name: Optional[str]) -> dict:
    """
    Build the study_solve result after the solver returns.

    COMSOL can return from a study run without computing a solution (for
    example when the model has no physics). This check makes that a failure.
    It reads every solution in the model, so an older computed solution also
    counts as computed.
    """
    status = solution_status(model)
    computed = sorted(t for t, ok in status.items() if ok)
    empty = sorted(t for t, ok in status.items() if not ok)
    result = {
        "success": bool(computed),
        "study": study_name,
        "computed_solutions": computed,
        "empty_solutions": empty,
    }
    if computed:
        result["message"] = "Solving completed."
    else:
        result["error"] = (
            "The study ran, but no solution holds data. "
            "Check that the model has physics, a material, and a mesh."
        )
    return result


STUDY_STEPS = {"Stationary": "stat", "Transient": "time"}


def create_study(model, study_type: str = "Stationary", study_tag: Optional[str] = None) -> dict:
    """Create a study with one step and no custom solver."""
    if study_type not in STUDY_STEPS:
        return {"success": False, "error": f"Unknown study_type: {study_type}. Use one of {sorted(STUDY_STEPS)}."}
    studies = model.java.study()
    existing = {str(t) for t in studies.tags()}
    if study_tag is None:
        i = 1
        while f"std{i}" in existing:
            i += 1
        study_tag = f"std{i}"
    elif study_tag in existing:
        return {"success": False, "error": f"Study already exists: {study_tag}"}
    step = STUDY_STEPS[study_type]
    try:
        studies.create(study_tag).create(step, study_type)
    except Exception as e:
        return {"success": False, "error": f"Failed to create study: {str(e).strip().splitlines()[0]}"}
    return {"success": True, "study": {"tag": study_tag, "type": study_type, "step": step}}


def configure_step(
    model,
    study_tag: str,
    step_tag: str = "stat",
    solve_for: Optional[dict] = None,
    properties: Optional[dict] = None,
) -> dict:
    """Choose the physics a study step solves and set the properties of the step."""
    studies = model.java.study()
    if study_tag not in [str(t) for t in studies.tags()]:
        return {"success": False, "error": f"Study not found: {study_tag}"}
    step = model.java.study(study_tag).feature(step_tag)
    rejected = {}
    for path, active in (solve_for or {}).items():
        try:
            step.setSolveFor(path, bool(active))
        except Exception as e:
            rejected[path] = str(e).strip().splitlines()[0]
    for name, value in (properties or {}).items():
        try:
            step.set(name, [str(v) for v in value] if isinstance(value, (list, tuple)) else str(value))
        except Exception as e:
            rejected[name] = str(e).strip().splitlines()[0]
    result = {"success": not rejected, "study": study_tag, "step": step_tag,
              "solve_for": dict(solve_for or {}), "properties": dict(properties or {})}
    if rejected:
        result["rejected"] = rejected
        result["error"] = f"COMSOL rejected {sorted(rejected)}."
    return result


def _solver_tree(node) -> dict:
    """Return the feature tags and types under a solver node."""
    return {str(tag): {"type": str(node.feature(tag).getType()), **_solver_tree(node.feature(tag))}
            for tag in node.feature().tags()}


def configure_solver(model, study_tag: str, settings: dict) -> dict:
    """
    Create the default solver sequence of a study if it has none, then set solver properties.

    settings maps a feature type (first match) or feature tag to its properties, for
    example {"Time": {"maxstepbdf": "1e-4"}} or {"t1": {"tstepsbdf": "strict"}}. The
    sequence is attached to the study, or the next solve builds a new default sequence.
    """
    jm = model.java
    if study_tag not in [str(t) for t in jm.study().tags()]:
        return {"success": False, "error": f"Study not found: {study_tag}"}
    sol_tag = None
    for tag in jm.sol().tags():
        if str(jm.sol(tag).study()) == study_tag:
            sol_tag = str(tag)
    if sol_tag is None:
        existing = [str(t) for t in jm.sol().tags()]
        i = 1
        while f"sol{i}" in existing:
            i += 1
        sol_tag = f"sol{i}"
        sol = jm.sol().create(sol_tag)
        sol.study(study_tag)
        sol.attach(study_tag)
        sol.createAutoSequence(study_tag)
    sol = jm.sol(sol_tag)

    def find(node, key):
        for tag in node.feature().tags():
            child = node.feature(tag)
            if key in (str(tag), str(child.getType())):
                return child
            found = find(child, key)
            if found is not None:
                return found
        return None

    rejected = {}
    for key, properties in settings.items():
        node = find(sol, key)
        if node is None:
            rejected[key] = "feature not found"
            continue
        for name, value in properties.items():
            try:
                node.set(name, str(value))
            except Exception as e:
                rejected[f"{key}.{name}"] = str(e).strip().splitlines()[0]
    result = {"success": not rejected, "study": study_tag, "solution": sol_tag}
    if rejected:
        result["rejected"] = rejected
        result["error"] = f"COMSOL rejected {sorted(rejected)}."
    result.update({"settings": dict(settings), "features": _solver_tree(sol)})
    return result


def register_study_tools(mcp: FastMCP) -> None:
    """Register study and solving tools with the MCP server."""
    
    @mcp.tool()
    def study_list(model_name: Optional[str] = None) -> dict:
        """
        List all studies in a model.
        
        Args:
            model_name: Model name (default: current model)
        
        Returns:
            List of study names with their types
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            studies = model.studies()
            
            study_info = []
            for study_name in studies:
                info = {"name": study_name}
                try:
                    study_node = model / "studies" / study_name
                    children = [child.name() for child in study_node.children()]
                    info["steps"] = children
                except Exception:
                    pass
                study_info.append(info)
            
            return {
                "success": True,
                "studies": study_info,
                "count": len(study_info),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list studies: {str(e)}"}
    
    @mcp.tool()
    def study_create(
        study_type: str = "Stationary",
        study_tag: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Create a study with one step. COMSOL makes its default solver at the solve.

        The default solver fits the physics, for example an iterative solver
        for a large 3D flow. Use solver_fully_coupled for manual control.

        Args:
            study_type: "Stationary" or "Transient"
            study_tag: Study tag (default: std1, std2, ...)
            model_name: Model name (default: current model)

        Returns:
            The study tag and step
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return create_study(model, study_type, study_tag)

    @mcp.tool()
    def study_configure_solver(
        study_tag: str,
        settings: dict,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Set properties of a study's solver sequence, and create the default sequence if needed.

        Args:
            study_tag: Study tag, e.g. "std1"
            settings: Feature type or tag mapped to properties. Example for a fixed
                maximum time step: {"Time": {"tstepsbdf": "strict", "maxstepconstraintbdf": "const", "maxstepbdf": "1e-4"}}
            model_name: Model name (default: current model)

        Returns:
            The solution tag, the settings, and any setting COMSOL rejected
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return configure_solver(model, study_tag, settings)

    @mcp.tool()
    def study_configure_step(
        study_tag: str,
        step_tag: str = "stat",
        solve_for: Optional[dict] = None,
        properties: Optional[dict] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Choose the physics a study step solves, and set step properties.

        Use it to solve one physics at a time. Example: flow first with
        solve_for {"/physics/ht": False, "/multiphysics/noni1": False}, and a speed
        ramp with properties {"useparam": "on", "pname": ["V0"],
        "plistarr": ["0.2 0.5 1 2"], "punit": ["m/s"]}.

        Args:
            study_tag: Study tag
            step_tag: Step tag (default: "stat")
            solve_for: Physics or multiphysics paths mapped to True or False
            properties: COMSOL properties of the step
            model_name: Model name (default: current model)

        Returns:
            The settings applied, and anything COMSOL rejected
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return configure_step(model, study_tag, step_tag, solve_for, properties)

    @mcp.tool()
    def study_solve(
        study_name: Optional[str] = None,
        model_name: Optional[str] = None,
        wait: bool = True,
        timeout: Optional[float] = None
    ) -> dict:
        """
        Solve a study (synchronous by default).
        
        Args:
            study_name: Study to solve (None for all studies)
            model_name: Model name (default: current model)
            wait: If True, wait for completion; if False, return immediately
            timeout: Maximum wait time in seconds (only used if wait=True)
        
        Returns:
            Solution status, or error message
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        if async_solver.is_running:
            return {
                "success": False,
                "error": "Another solving operation is in progress. Use study_get_progress to check status."
            }
        
        try:
            if wait:
                model.solve(study_name)
                return check_solved(model, study_name)
            else:
                started = async_solver.start_solve(model, study_name)
                if started:
                    return {
                        "success": True,
                        "study": study_name,
                        "message": "Solving started in background. Use study_get_progress to monitor.",
                        "async": True,
                    }
                else:
                    return {
                        "success": False,
                        "error": "Failed to start async solver."
                    }
        except Exception as e:
            return {"success": False, "error": f"Failed to solve: {str(e)}"}
    
    @mcp.tool()
    def study_solve_async(
        study_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Start solving a study in the background (asynchronous).
        
        Use study_get_progress to monitor progress and study_cancel to stop.
        
        Args:
            study_name: Study to solve (None for all studies)
            model_name: Model name (default: current model)
        
        Returns:
            Confirmation that solving started, or error message
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        if async_solver.is_running:
            progress = async_solver.get_progress()
            return {
                "success": False,
                "error": "Another solving operation is already in progress.",
                "current_progress": progress,
            }
        
        try:
            started = async_solver.start_solve(model, study_name)
            if started:
                return {
                    "success": True,
                    "study": study_name,
                    "model": model.name(),
                    "message": "Solving started in background.",
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to start async solver."
                }
        except Exception as e:
            return {"success": False, "error": f"Failed to start solving: {str(e)}"}
    
    @mcp.tool()
    def study_get_progress() -> dict:
        """
        Get the progress of the current solving operation.
        
        Returns:
            Progress information including status, percentage, and elapsed time
        """
        progress = async_solver.get_progress()
        return {
            "success": True,
            "progress": progress,
        }
    
    @mcp.tool()
    def study_cancel() -> dict:
        """
        Cancel the current solving operation.
        
        Note: The solver may take a moment to respond to cancellation.
        
        Returns:
            Cancellation status
        """
        if async_solver.cancel():
            return {
                "success": True,
                "message": "Cancellation requested. Solver will stop at next checkpoint.",
            }
        return {
            "success": False,
            "message": "No solving operation in progress.",
        }
    
    @mcp.tool()
    def study_wait(timeout: Optional[float] = None) -> dict:
        """
        Wait for the current solving operation to complete.
        
        Args:
            timeout: Maximum time to wait in seconds (None for indefinite)
        
        Returns:
            Final progress status
        """
        completed = async_solver.wait(timeout=timeout)
        progress = async_solver.get_progress()
        
        return {
            "success": True,
            "completed": completed,
            "progress": progress,
        }
    
    @mcp.tool()
    def solutions_list(model_name: Optional[str] = None) -> dict:
        """
        List all solutions in a model.
        
        Args:
            model_name: Model name (default: current model)
        
        Returns:
            List of solution configurations
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            solutions = model.solutions()
            return {
                "success": True,
                "solutions": solutions,
                "count": len(solutions),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list solutions: {str(e)}"}
    
    @mcp.tool()
    def datasets_list(model_name: Optional[str] = None) -> dict:
        """
        List all datasets in a model.
        
        Datasets represent solution data that can be evaluated or visualized.
        
        Args:
            model_name: Model name (default: current model)
        
        Returns:
            List of dataset names
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            datasets = model.datasets()
            return {
                "success": True,
                "datasets": datasets,
                "count": len(datasets),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list datasets: {str(e)}"}
