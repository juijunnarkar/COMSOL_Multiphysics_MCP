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


def _study_solution(jm, study_tag: str) -> Optional[str]:
    """Return the tag of the last solution that belongs to a study, or None."""
    sol_tag = None
    for tag in jm.sol().tags():
        if str(jm.sol(tag).study()) == study_tag:
            sol_tag = str(tag)
    return sol_tag


def _study_label(jm, study_tag: str) -> Optional[str]:
    """Return the label of a study, for example "Study 3". MPh solves a study by its label."""
    try:
        return str(jm.study(study_tag).label())
    except Exception:
        return None


def _find_feature(node, key):
    """Return the first feature under node, depth first, whose tag or type is key."""
    for tag in node.feature().tags():
        child = node.feature(tag)
        if key in (str(tag), str(child.getType())):
            return child
        found = _find_feature(child, key)
        if found is not None:
            return found
    return None


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
    sol_tag = _study_solution(jm, study_tag)
    created = sol_tag is None
    if created:
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

    rejected = {}
    for key, properties in settings.items():
        node = _find_feature(sol, key)
        if node is None:
            rejected[key] = "feature not found"
            continue
        for name, value in properties.items():
            try:
                node.set(name, str(value))
            except Exception as e:
                rejected[f"{key}.{name}"] = str(e).strip().splitlines()[0]
    result = {"success": not rejected, "study": study_tag, "solution": sol_tag, "sequence_created": created}
    if rejected:
        result["rejected"] = rejected
        result["error"] = f"COMSOL rejected {sorted(rejected)}."
    result.update({"settings": dict(settings), "features": _solver_tree(sol)})
    return result


def link_solution(
    model,
    study_tag: str,
    from_study: str,
    step_tag: Optional[str] = None,
    initial: bool = False,
    not_solved: bool = True,
    solnum: str = "last",
) -> dict:
    """
    Make a study read the solution of another, solved study.

    initial=True takes the initial values from that solution. not_solved=True takes
    the values of the variables that the step does not solve for from it. solnum
    picks the stored state, for example "last" for the last output time.

    The step settings reach a solver sequence only when COMSOL creates it. An
    existing sequence keeps its own copy in its Variables feature (v1), so this
    function sets v1 too.
    """
    jm = model.java
    studies = [str(t) for t in jm.study().tags()]
    for tag in (study_tag, from_study):
        if tag not in studies:
            return {"success": False, "error": f"Study not found: {tag}"}
    if not (initial or not_solved):
        return {"success": False, "error": "Set initial, not_solved, or both to True."}
    source = _study_solution(jm, from_study)
    if source is None or bool(jm.sol(source).isEmpty()):
        return {"success": False, "error": f"Study {from_study} has no computed solution. Solve it first."}
    if step_tag is None:
        step_tag = str(jm.study(study_tag).feature().tags()[0])

    step_properties, v1 = {}, {}
    if initial:
        step_properties.update({"useinitsol": "on", "initmethod": "sol", "initstudy": from_study, "solnum": solnum})
        v1.update({"initmethod": "sol", "initsol": source, "solnum": solnum})
    if not_solved:
        step_properties.update({"usesol": "on", "notsolmethod": "sol", "notstudy": from_study, "notsolnum": solnum})
        v1.update({"notsolmethod": "sol", "notsol": source, "notsolnum": solnum})
    # Set the step first, so that a sequence created next starts from these settings.
    step = configure_step(model, study_tag, step_tag, properties=step_properties)
    solver = configure_solver(model, study_tag, {"v1": v1})
    rejected = {**step.get("rejected", {}), **solver.get("rejected", {})}
    result = {"success": not rejected, "study": study_tag, "label": _study_label(jm, study_tag),
              "step": step_tag, "from_study": from_study,
              "from_solution": source, "solution": solver.get("solution"), "step_properties": step_properties,
              "variables": v1}
    if rejected:
        result["rejected"] = rejected
        result["error"] = f"COMSOL rejected {sorted(rejected)}."
    return result


LINEAR_SOLVERS = {"direct": "Direct", "iterative": "Iterative"}


def _linear_solver_tag(tree: dict, kind: str) -> Optional[str]:
    """
    Return the tag of a linear solver of a type in a solver tree from _solver_tree.

    The default sequence also holds a dDef node of type Direct, and a coarse grid
    solver d1 inside the iterative solver (i1/mg1/cs/d1, COMSOL 6.4). Prefer the
    shallowest node, and a tag that does not end in "Def", such as t1/d1.
    """
    found = []

    def walk(node, depth):
        for tag, child in node.items():
            if isinstance(child, dict):
                if child.get("type") == kind:
                    found.append((depth, tag.endswith("Def"), tag))
                walk(child, depth + 1)
    walk(tree, 0)
    return min(found)[2] if found else None


def configure_transient(
    model,
    study_tag: str,
    from_study: str,
    tlist: str,
    rtol: Optional[str] = None,
    solve_for: Optional[dict] = None,
    step_tag: str = "time",
    solnum: str = "last",
    consistent: Optional[str] = "off",
    linear_solver: Optional[str] = "direct",
    jacobian_update: Optional[str] = "onevery",
) -> dict:
    """
    Set up a Transient study that starts from the solution of a solved study.

    It creates the study if it does not exist. Then it sets the output times,
    the tolerance, and the physics to solve. It takes the initial values and the
    values of the variables it does not solve for from from_study. Last, it
    creates the solver sequence and sets the time solver options. A value of
    None for consistent, linear_solver, or jacobian_update keeps the COMSOL default.
    """
    jm = model.java
    created = study_tag not in [str(t) for t in jm.study().tags()]
    existing_sequence = not created and _study_solution(jm, study_tag) is not None
    if created:
        made = create_study(model, "Transient", study_tag)
        if not made["success"]:
            return made
    properties = {"tlist": tlist}
    if rtol is not None:
        properties.update({"usertol": "on", "rtol": str(rtol)})
    step = configure_step(model, study_tag, step_tag, solve_for, properties)
    if not step["success"]:
        return step
    link = link_solution(model, study_tag, from_study, step_tag, initial=True, not_solved=True, solnum=solnum)
    if not link["success"]:
        return link

    tree = configure_solver(model, study_tag, {})["features"]
    settings = {}
    if consistent is not None:
        settings["Time"] = {"consistent": consistent}
    coupled = {}
    if linear_solver is not None:
        kind = LINEAR_SOLVERS.get(linear_solver)
        tag = _linear_solver_tag(tree, kind) if kind else linear_solver
        if tag is None:
            return {"success": False, "error": f"The solver sequence of {study_tag} has no {kind} linear solver.",
                    "features": tree}
        coupled["linsolver"] = tag
    if jacobian_update is not None:
        coupled["jtech"] = jacobian_update
    if coupled:
        settings["FullyCoupled"] = coupled
    solver = configure_solver(model, study_tag, settings)
    result = {"success": solver["success"], "study": study_tag, "label": _study_label(jm, study_tag),
              "step": step_tag, "created": created,
              "from_study": from_study, "from_solution": link["from_solution"], "solution": solver["solution"],
              "step_properties": {**properties, **link["step_properties"]}, "solve_for": dict(solve_for or {}),
              "solver_settings": settings, "features": solver["features"]}
    if not solver["success"]:
        result["rejected"] = solver["rejected"]
        result["error"] = solver["error"]
    if existing_sequence:
        result["warning"] = ("The study already had a solver sequence. COMSOL may not copy the new tlist and "
                             "rtol of the step into it. Check the Time feature, or remove the solution and run again.")
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
    def study_transient_from_solution(
        study_tag: str,
        from_study: str,
        tlist: str,
        rtol: Optional[str] = None,
        solve_for: Optional[dict] = None,
        solnum: str = "last",
        consistent: Optional[str] = "off",
        linear_solver: Optional[str] = "direct",
        jacobian_update: Optional[str] = "onevery",
        model_name: Optional[str] = None
    ) -> dict:
        """
        Set up a Transient study that starts from the last state of a solved study.

        Use it when a steady solve does not converge, to see whether the flow
        settles or keeps moving. The tool creates the study if it does not exist.
        It takes the initial values, and the values of the physics it does not
        solve, from from_study. Then it creates the solver sequence. Run it on a
        study with no solver sequence: an existing sequence may keep its old times.

        The solver defaults come from a 193 000 DOF laminar flow cell (COMSOL 6.4):
        - consistent "off": with the consistent start step on, Newton did not
          converge at t = 1e-7 s with the direct or the iterative linear solver.
        - linear_solver "direct": in steady runs of the same cell, the direct
          solver took 2 s per iteration on 32 cores, against 14 s for the
          iterative solver. It needs more memory.
        - jacobian_update "onevery": a new Jacobian at each Newton step. The
          working runs used it. Its effect alone was not tested.
        Pass None to keep the COMSOL default for an option.

        Limits: the BDF time solver damps changes faster than its step. A run
        with 1 ms steps cannot resolve 1 kHz vortex shedding. Symmetry planes
        allow only symmetric motion.

        Args:
            study_tag: Tag of the Transient study, e.g. "std3" (created if missing)
            from_study: Tag of the solved study to start from, e.g. "std1"
            tlist: Output times, e.g. "range(0,1e-3,0.06)". The solver picks its own steps.
            rtol: Relative tolerance, e.g. "1e-3" (default: COMSOL default)
            solve_for: Physics or multiphysics paths mapped to True or False,
                e.g. {"/physics/ht": False, "/multiphysics/noni1": False} for flow only
            solnum: Stored state of from_study to use (default: "last")
            consistent: Consistent initialization of the time solver, e.g. "off".
                None keeps the COMSOL default.
            linear_solver: "direct", "iterative", a linear solver tag such as "d1",
                or None to keep the COMSOL default
            jacobian_update: Jacobian update of the fully coupled solver, e.g.
                "onevery" or "minimal". None keeps the COMSOL default.
            model_name: Model name (default: current model)

        Returns:
            The step properties, the solver settings, the solver feature tree,
            and anything COMSOL rejected
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return configure_transient(model, study_tag, from_study, tlist, rtol, solve_for, "time", solnum,
                                   consistent, linear_solver, jacobian_update)

    @mcp.tool()
    def study_use_solution(
        study_tag: str,
        from_study: str,
        step_tag: Optional[str] = None,
        initial: bool = False,
        not_solved: bool = True,
        solnum: str = "last",
        model_name: Optional[str] = None
    ) -> dict:
        """
        Make a study read the solution of another, solved study.

        Example: solve heat (std2) on the last flow state of a Transient flow
        study (std3) with study_use_solution("std2", "std3"). The values of the
        physics that std2 does not solve come from the last time of std3.

        The tool sets the study step and the Variables feature (v1) of the solver
        sequence. An existing sequence keeps its own copy of the step settings,
        so the step alone is not enough.

        Args:
            study_tag: Study that reads the solution, e.g. "std2"
            from_study: Solved study to read, e.g. "std3"
            step_tag: Step of study_tag (default: its first step)
            initial: Take the initial values from from_study
            not_solved: Take the values of the variables that the step does not solve for
            solnum: Stored state to use (default: "last")
            model_name: Model name (default: current model)

        Returns:
            The step properties, the v1 settings, and anything COMSOL rejected
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return link_solution(model, study_tag, from_study, step_tag, initial, not_solved, solnum)

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
