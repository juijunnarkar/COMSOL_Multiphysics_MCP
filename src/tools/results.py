"""Results evaluation and export tools for COMSOL MCP Server."""

from typing import Optional, Union, Sequence
from pathlib import Path
from mcp.server.fastmcp import FastMCP

from .physics import _component_with_geometry, _numbered_tag
from .session import session_manager


STAT_OPERATIONS = {"max": "Max", "min": "Min", "average": "Av", "integral": "Int"}
STAT_DIMENSIONS = {3: {"domain": "Volume", "boundary": "Surface"}, 2: {"domain": "Surface", "boundary": "Line"}}


def _space_dimension(model) -> int:
    """Space dimension of the first geometry. A model with no readable geometry counts as 3D."""
    try:
        comp, geom_tag = _component_with_geometry(model.java)
        return int(comp.geom(geom_tag).getSDim())
    except Exception:
        return 3


def evaluate_stat(
    model,
    operation: str,
    expression: str,
    entity_type: str,
    entities: Sequence[int],
    unit: Optional[str] = None,
    dataset: Optional[str] = None,
) -> dict:
    """
    Evaluate a max, min, average, or integral over 2D or 3D domains or boundaries.

    The value comes from a COMSOL numerical node, which the function removes
    after it reads the value. The result reports the entities COMSOL applied.
    """
    if operation not in STAT_OPERATIONS:
        return {"success": False, "error": f"Unknown operation: {operation}. Use one of {sorted(STAT_OPERATIONS)}."}
    if entity_type not in STAT_DIMENSIONS[3]:
        return {"success": False, "error": f"Unknown entity_type: {entity_type}. Use one of {sorted(STAT_DIMENSIONS[3])}."}
    try:
        requested = [int(e) for e in entities]
    except (TypeError, ValueError):
        return {"success": False, "error": f"Entity numbers must be integers: {entities}"}
    if not requested or any(e < 1 for e in requested):
        return {"success": False, "error": f"Entity numbers must be 1 or greater: {entities}"}

    numerical = model.java.result().numerical()
    existing = {str(t) for t in numerical.tags()}
    i = 1
    while f"stat{i}" in existing:
        i += 1
    tag = f"stat{i}"
    names = STAT_DIMENSIONS.get(_space_dimension(model), STAT_DIMENSIONS[3])
    node_type = STAT_OPERATIONS[operation] + names[entity_type]
    try:
        node = numerical.create(tag, node_type)
        try:
            if dataset:
                node.set("data", dataset)
            node.selection().set(requested)
            applied = [int(e) for e in node.selection().entities()]
            missing = sorted(set(requested) - set(applied))
            if missing:
                return {"success": False, "error": f"COMSOL did not apply {entity_type} entities {missing}."}
            node.set("expr", [expression])
            if unit:
                node.set("unit", [unit])
            value = float(node.getReal()[0][0])
        finally:
            numerical.remove(tag)
    except Exception as e:
        return {"success": False, "error": f"Failed to evaluate {operation} of {expression}: {str(e).strip().splitlines()[0]}"}
    return {
        "success": True,
        "operation": operation,
        "expression": expression,
        "entity_type": entity_type,
        "entities": applied,
        "unit": unit,
        "value": value,
    }


def _set_properties(node, properties: Optional[dict]) -> None:
    for name, value in (properties or {}).items():
        # COMSOL has no single set overload for a Python int, so numbers go as strings.
        if isinstance(value, (list, tuple)):
            value = [[str(v) for v in row] if isinstance(row, (list, tuple)) else str(row) for row in value]
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        node.set(name, value)


def _select(node, spec: dict) -> None:
    """Apply a selection from a plot feature spec. geometry and dimension are optional."""
    if spec.get("entities") is None:
        return
    selection = node.selection()
    if spec.get("geometry"):
        selection.geom(spec["geometry"], int(spec["dimension"]))
    selection.set([int(e) for e in spec["entities"]])


def add_dataset(model, dataset_type: str, properties: Optional[dict] = None, label: Optional[str] = None) -> dict:
    """Add a results dataset, such as a CutPlane, CutLine3D, Mirror3D, or Array3D."""
    datasets = model.java.result().dataset()
    tag = _numbered_tag(datasets, "rds")
    try:
        node = datasets.create(tag, dataset_type)
        _set_properties(node, properties)
        if label:
            node.label(label)
    except Exception as e:
        return {"success": False, "error": f"Failed to add dataset {dataset_type}: {str(e).splitlines()[0]}"}
    return {"success": True, "dataset": tag, "type": dataset_type}


def add_plot(
    model,
    group_type: str,
    features: Sequence[dict],
    dataset: Optional[str] = None,
    label: Optional[str] = None,
    properties: Optional[dict] = None,
) -> dict:
    """
    Add a plot group with its plot features, then run it.

    Each feature is a dict with type, and optional properties, entities, geometry, dimension,
    and subfeatures. A subfeature has the same keys, for example a Color Expression on a Streamline.
    """
    if not features:
        return {"success": False, "error": "Give at least one plot feature."}
    result = model.java.result()
    tag = _numbered_tag(result, "pg")
    try:
        group = result.create(tag, group_type)
        if dataset:
            group.set("data", dataset)
        _set_properties(group, properties)
        if label:
            group.label(label)
        made = []
        for spec in features:
            ftag = f"{spec['type'][:4].lower()}{len(made) + 1}"
            node = group.create(ftag, spec["type"])
            _set_properties(node, spec.get("properties"))
            _select(node, spec)
            for j, sub in enumerate(spec.get("subfeatures", ()), start=1):
                child = node.create(f"{sub['type'][:3].lower()}{j}", sub["type"])
                _set_properties(child, sub.get("properties"))
                _select(child, sub)
            made.append(ftag)
        group.run()
    except Exception as e:
        detail = " ".join(str(e).split())[:500]
        return {"success": False, "error": f"Failed to add plot {group_type}: {detail}"}
    return {"success": True, "plot_group": tag, "features": made}


def clear_plots(model) -> dict:
    """Remove all plot groups and the datasets that add_dataset made, so a rerun starts clean."""
    result = model.java.result()
    groups = [str(t) for t in result.tags()]
    for tag in groups:
        result.remove(tag)
    added = [str(t) for t in result.dataset().tags() if str(t).startswith("rds")]
    for tag in added:
        result.dataset().remove(tag)
    return {"success": True, "plot_groups": groups, "datasets": added}


def export_plot_image(model, plot_group: str, file_path: str, width: int = 1400, height: int = 900, font_size: int = 14) -> dict:
    """Write a plot group to a PNG file with a white background. The Image node is removed after it runs."""
    exports = model.java.result().export()
    tag = _numbered_tag(exports, "pimg")
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        node = exports.create(tag, plot_group, "Image")
        try:
            # Without background = color, a server export gives a transparent PNG, which a dark viewer shows as black.
            _set_properties(node, {"imagetype": "png", "pngfilename": str(file_path), "size": "manualweb", "unit": "px",
                                   "width": str(width), "height": str(height), "fontsize": str(font_size),
                                   "background": "color", "options1d": "on", "options2d": "on", "options3d": "on",
                                   "logo1d": "off", "logo2d": "off", "logo3d": "off"})
            node.run()
        finally:
            exports.remove(tag)
    except Exception as e:
        return {"success": False, "error": f"Failed to export {plot_group}: {' '.join(str(e).split())[:400]}"}
    return {"success": True, "plot_group": plot_group, "file": str(file_path)}


def solution_dataset(model, study_tag: str) -> Optional[str]:
    """
    Return the MPh name of the Solution dataset that shows the solution of a study, or None.

    COMSOL 6.4 labels the dataset "Study 3/Solution 3". MPh reads "/" as a path
    separator, so its name for that dataset is "Study 3//Solution 3".
    """
    from .study import _study_solution

    jm = model.java
    sol_tag = _study_solution(jm, study_tag)
    if sol_tag is None:
        return None
    datasets = jm.result().dataset()
    for tag in datasets.tags():
        node = jm.result().dataset(tag)
        try:
            if str(node.getType()) == "Solution" and str(node.getString("solution")) == sol_tag:
                return str(node.label()).replace("/", "//")
        except Exception:
            continue
    return None


def probe_time_series(
    model,
    points: Sequence[Sequence[float]],
    expressions: Union[str, Sequence[str]],
    dataset: Optional[str] = None,
    study_tag: Optional[str] = None,
    names: Optional[Sequence[str]] = None,
    unit: Optional[str] = None,
) -> dict:
    """
    Evaluate expressions at points against time.

    MPh cannot evaluate on a CutPoint3D dataset: it reports that the dataset does
    not refer to a solution. This function evaluates at3(x, y, z, expr) (at2 in 2D)
    on the solution dataset itself. Each at3 term is a global expression, so MPh
    returns one row per output time and one column per expression.
    """
    import numpy as np

    expressions = [expressions] if isinstance(expressions, str) else list(expressions)
    points = [[float(c) for c in p] for p in points]
    if not points or not expressions:
        return {"success": False, "error": "Give at least one point and one expression."}
    dims = {len(p) for p in points}
    sdim = _space_dimension(model)
    if dims != {sdim}:
        return {"success": False, "error": f"Each point needs {sdim} coordinates, for a {sdim}D model."}
    names = list(names) if names is not None else [f"p{i + 1}" for i in range(len(points))]
    if len(names) != len(points):
        return {"success": False, "error": "Give one name per point."}
    if dataset is None and study_tag is not None:
        dataset = solution_dataset(model, study_tag)
        if dataset is None:
            return {"success": False, "error": f"No Solution dataset shows the solution of {study_tag}."}

    operator = f"at{sdim}"
    terms = [f"{operator}({','.join(f'{c:.9g}' for c in p)},{e})" for p in points for e in expressions]

    def run(columns):
        units = None if unit is None else (["s"] if columns[0] == "t" else []) + [unit] * len(terms)
        raw = model.evaluate(columns, unit=units, dataset=dataset)
        return np.asarray(raw, dtype=float).reshape(-1, len(columns))

    columns = ["t"] + terms
    try:
        try:
            table = run(columns)
        except Exception as e:
            # A Stationary solution has no time variable t (COMSOL 6.4: "Undefined variable. - Variable: t").
            if "Variable: t" not in " ".join(str(e).split()):
                raise
            columns = terms
            table = run(columns)
    except Exception as e:
        return {"success": False, "error": f"Failed to evaluate the probes: {' '.join(str(e).split())[:400]}",
                "expressions": columns}
    first = 1 if columns[0] == "t" else 0
    probes = []
    for i, (name, point) in enumerate(zip(names, points)):
        start = first + i * len(expressions)
        probes.append({"name": name, "point": point,
                       "values": {e: table[:, start + j].tolist() for j, e in enumerate(expressions)}})
    return {"success": True, "dataset": dataset, "unit": unit, "n_times": int(table.shape[0]),
            "times": table[:, 0].tolist() if first else None, "probes": probes}


def register_results_tools(mcp: FastMCP) -> None:
    """Register results tools with the MCP server."""

    @mcp.tool()
    def results_probe_time_series(
        points: Sequence[Sequence[float]],
        expressions: Union[str, Sequence[str]],
        dataset: Optional[str] = None,
        study_tag: Optional[str] = None,
        names: Optional[Sequence[str]] = None,
        unit: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Read expressions at points for every output time of a solution.

        Use it to watch a Transient study: if every probe settles, the flow is
        steady. The tool evaluates at3(x,y,z,expr) (at2 in 2D) on the solution
        dataset. A CutPoint3D dataset does not work through MPh.

        Example: results_probe_time_series([[0.02, 0.001, 0.0125]], ["u", "p"],
        study_tag="std3", names=["channel_mid"]).

        Args:
            points: Point coordinates in model length units, one list per point
            expressions: Expression(s) to read at each point, e.g. "u" or ["u", "T"]
            dataset: Solution dataset label, e.g. "Study 3//Solution 3"
            study_tag: Study tag. The tool finds its Solution dataset. Used when dataset is None.
            names: One name per point (default: p1, p2, ...)
            unit: Unit for every expression, e.g. "m/s" (default: model units)
            model_name: Model name (default: current model)

        Returns:
            The output times (None for a Stationary solution), and for each probe
            its point and one value list per expression
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return probe_time_series(model, points, expressions, dataset, study_tag, names, unit)

    @mcp.tool()
    def results_evaluate(
        expression: Union[str, Sequence[str]],
        unit: Optional[str] = None,
        dataset: Optional[str] = None,
        inner: Optional[Union[int, str, Sequence[int]]] = None,
        outer: Optional[Union[int, Sequence[int]]] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Evaluate an expression on a solution dataset.
        
        Args:
            expression: Expression(s) to evaluate, e.g., "es.normE" or ["x", "y", "es.normE"]
            unit: Desired unit for result, e.g., "V/m", "pF"
            dataset: Dataset name (default: uses default dataset)
            inner: For time-dependent solutions: index, 'first', 'last', or list of indices
            outer: For parametric sweeps: index or list of indices
            model_name: Model name (default: current model)
        
        Returns:
            Evaluated values as lists, or error message
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            result = model.evaluate(
                expression,
                unit=unit,
                dataset=dataset,
                inner=inner,
                outer=outer,
            )
            
            import numpy as np
            if isinstance(result, np.ndarray):
                if result.ndim == 0:
                    value = float(result)
                else:
                    value = result.tolist()
            elif isinstance(result, (list, tuple)):
                value = [v.tolist() if hasattr(v, 'tolist') else v for v in result]
            else:
                value = result
            
            return {
                "success": True,
                "expression": expression,
                "unit": unit,
                "dataset": dataset,
                "value": value,
                "shape": getattr(result, 'shape', None),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to evaluate: {str(e)}"}
    
    @mcp.tool()
    def results_stat(
        operation: str,
        expression: str,
        entity_type: str,
        entities: Sequence[int],
        unit: Optional[str] = None,
        dataset: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Evaluate a max, min, average, or integral over chosen domains or boundaries of a 2D or 3D model.

        Example: the maximum temperature in domain 1 is
        results_stat("max", "T", "domain", [1], unit="degC").
        Get the numbers from geometry_get_domains and geometry_get_boundaries.

        Args:
            operation: "max", "min", "average", or "integral"
            expression: COMSOL expression, e.g. "T" or "ht.ntflux"
            entity_type: "domain" or "boundary"
            entities: Domain or boundary numbers
            unit: Result unit, e.g. "degC" (default: model unit)
            dataset: Dataset tag (default: first dataset of the model)
            model_name: Model name (default: current model)

        Returns:
            The value and the entities COMSOL applied, or an error
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return evaluate_stat(model, operation, expression, entity_type, entities, unit, dataset)

    @mcp.tool()
    def results_global_evaluate(
        expression: str,
        unit: Optional[str] = None,
        dataset: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Evaluate a global expression (returns a single scalar value).
        
        Common global expressions include:
        - Integration: "intop1(T)" where intop1 is an integration operator
        - Maximum: "maxop1(T)" 
        - Derived values: "2*es.intWe/U^2" for capacitance
        
        Args:
            expression: Global expression to evaluate
            unit: Desired unit for result
            dataset: Dataset name
            model_name: Model name (default: current model)
        
        Returns:
            The first value in "value", and all values in "values" when the
            dataset has several time steps or parameter values
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            result = model.evaluate(expression, unit=unit, dataset=dataset)
            
            import numpy as np
            values = np.asarray(result, dtype=float).flatten()
            answer = {
                "success": True,
                "expression": expression,
                "unit": unit,
                "value": float(values[0]),
            }
            if values.size > 1:
                answer["values"] = values.tolist()
            return answer
        except Exception as e:
            return {"success": False, "error": f"Failed to evaluate global expression: {str(e)}"}
    
    @mcp.tool()
    def results_inner_values(
        dataset: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Get inner solution indices and values (time steps in time-dependent study).
        
        Args:
            dataset: Dataset name (default: default dataset)
            model_name: Model name (default: current model)
        
        Returns:
            Arrays of indices and corresponding values (e.g., time values)
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            indices, values = model.inner(dataset)
            
            return {
                "success": True,
                "dataset": dataset,
                "indices": indices.tolist() if hasattr(indices, 'tolist') else list(indices),
                "values": values.tolist() if hasattr(values, 'tolist') else list(values),
                "count": len(values),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to get inner values: {str(e)}"}
    
    @mcp.tool()
    def results_outer_values(
        dataset: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Get outer solution indices and values (parameter values in parametric sweep).
        
        Args:
            dataset: Dataset name (default: default dataset)
            model_name: Model name (default: current model)
        
        Returns:
            Arrays of indices and corresponding parameter values
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            indices, values = model.outer(dataset)
            
            return {
                "success": True,
                "dataset": dataset,
                "indices": indices.tolist() if hasattr(indices, 'tolist') else list(indices),
                "values": values.tolist() if hasattr(values, 'tolist') else list(values),
                "count": len(values),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to get outer values: {str(e)}"}
    
    @mcp.tool()
    def results_export_data(
        node_name: Optional[str] = None,
        file_path: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Export data from an export node.
        
        Args:
            node_name: Export node name (default: run all exports)
            file_path: Output file path (overrides node setting)
            model_name: Model name (default: current model)
        
        Returns:
            Export confirmation with file path
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            model.export(node_name, file_path)
            
            return {
                "success": True,
                "node": node_name,
                "file": file_path,
                "message": f"Export completed: {node_name or 'all exports'}",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to export data: {str(e)}"}
    
    @mcp.tool()
    def results_export_image(
        node_name: Optional[str] = None,
        file_path: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Export a plot as an image.
        
        Args:
            node_name: Plot export node name
            file_path: Output image path (e.g., "results.png", "field.png")
            model_name: Model name (default: current model)
        
        Returns:
            Export confirmation with file path
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            model.export(node_name, file_path)
            
            return {
                "success": True,
                "node": node_name,
                "file": file_path,
                "message": f"Image exported to: {file_path}",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to export image: {str(e)}"}
    
    @mcp.tool()
    def results_dataset_add(
        dataset_type: str,
        properties: Optional[dict] = None,
        label: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a results dataset that a plot can read.

        Example: a cut plane at z = 12 mm is results_dataset_add("CutPlane",
        {"data": "dset2", "quickplane": "xy", "quickz": "12[mm]"}).

        Args:
            dataset_type: COMSOL type, e.g. "CutPlane", "CutLine3D", "Mirror3D", "Array3D"
            properties: COMSOL property names and values
            label: Name shown in the model tree
            model_name: Model name (default: current model)

        Returns:
            The new dataset tag, or an error
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return add_dataset(model, dataset_type, properties, label)

    @mcp.tool()
    def results_plot_add(
        group_type: str,
        features: Sequence[dict],
        dataset: Optional[str] = None,
        label: Optional[str] = None,
        properties: Optional[dict] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a plot group with its plot features, and run it. The plot stays in the model.

        Each feature is a dict: type (e.g. "Surface", "Slice", "Streamline", "ArrowVolume",
        "LineGraph"), and optional properties, entities, geometry, dimension, and subfeatures.
        A subfeature has the same keys, e.g. a "Color" expression on a streamline, or a
        "Selection" with geometry and dimension to limit a surface plot to chosen faces.

        Args:
            group_type: "PlotGroup3D", "PlotGroup2D", or "PlotGroup1D"
            features: Plot features, in draw order
            dataset: Dataset tag for the group
            label: Name shown in the model tree
            properties: Plot group property names and values
            model_name: Model name (default: current model)

        Returns:
            The plot group tag and feature tags, or an error
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return add_plot(model, group_type, features, dataset, label, properties)

    @mcp.tool()
    def results_plots_clear(model_name: Optional[str] = None) -> dict:
        """
        Remove all plot groups, and the datasets added with results_dataset_add.

        Args:
            model_name: Model name (default: current model)

        Returns:
            The removed tags, or an error
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return clear_plots(model)

    @mcp.tool()
    def results_plot_image(
        plot_group: str,
        file_path: str,
        width: int = 1400,
        height: int = 900,
        font_size: int = 14,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Write a plot group to a PNG file with a white background and no logo.

        Start mphserver with -graphics for 3D plots.

        Args:
            plot_group: Plot group tag from results_plot_add
            file_path: Output PNG path
            width: Image width in pixels
            height: Image height in pixels
            font_size: Font size
            model_name: Model name (default: current model)

        Returns:
            The file path, or an error
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return export_plot_image(model, plot_group, file_path, width, height, font_size)

    @mcp.tool()
    def results_exports_list(model_name: Optional[str] = None) -> dict:
        """
        List all export nodes defined in a model.
        
        Args:
            model_name: Model name (default: current model)
        
        Returns:
            List of export node names
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            exports = model.exports()
            return {
                "success": True,
                "exports": exports,
                "count": len(exports),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list exports: {str(e)}"}
    
    @mcp.tool()
    def results_plots_list(model_name: Optional[str] = None) -> dict:
        """
        List all plot nodes defined in a model.
        
        Args:
            model_name: Model name (default: current model)
        
        Returns:
            List of plot node names
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            plots = model.plots()
            return {
                "success": True,
                "plots": plots,
                "count": len(plots),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list plots: {str(e)}"}
