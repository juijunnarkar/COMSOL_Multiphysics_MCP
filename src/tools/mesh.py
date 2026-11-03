"""Mesh tools for COMSOL MCP Server."""

from typing import Optional, Sequence, Union
from mcp.server.fastmcp import FastMCP

from .session import session_manager


def mesh_counts(java_mesh) -> dict:
    """Read the element and vertex counts of a built mesh sequence."""
    try:
        return {
            "num_elements": int(java_mesh.getNumElem()),
            "num_vertices": int(java_mesh.getNumVertex()),
        }
    except Exception as e:
        return {"counts_error": f"Could not read mesh counts: {str(e).strip().splitlines()[0]}"}


def mesh_quality(java_mesh, measure: str = "skewness") -> dict:
    """Read the minimum and mean element quality. 1 is the best and 0 is a degenerate element."""
    try:
        stat = java_mesh.stat()
        stat.setQualityMeasure(measure)
        return {
            "quality_measure": measure,
            "min_quality": round(float(stat.getMinQuality()), 4),
            "avg_quality": round(float(stat.getMeanQuality()), 4),
        }
    except Exception as e:
        return {"quality_error": f"Could not read mesh quality: {str(e).strip().splitlines()[0]}"}


def set_element_sizes(mesh_seq, hmax: Optional[float], hmin: Optional[float]) -> None:
    """
    Set custom element sizes on the Size node of a mesh sequence.

    Custom sizes make the sequence user-controlled, and such a sequence has no
    mesher until one is added. Without it the sequence builds 0 elements.
    """
    if hmax is None and hmin is None:
        return
    size = mesh_seq.feature("size")
    size.set("custom", "on")
    mesh_seq.create("ftet1", "FreeTet")
    if hmax is not None:
        size.set("hmax", str(hmax))
    if hmin is not None:
        size.set("hmin", str(hmin))


def add_mesh_feature(
    model,
    feature_type: str,
    mesh_name: str = "mesh1",
    parent: Optional[str] = None,
    entity_dim: Optional[int] = None,
    entities=None,
    selections: Optional[dict] = None,
    properties: Optional[dict] = None,
) -> dict:
    """
    Add a step to a mesh sequence, or a sub-step under the step named by parent.

    entities is a list of numbers, or "remaining" for all entities without a
    mesh. selections maps a named selection of the step, such as "sourceface",
    to a list of numbers. Each selection is read back, and a rejected property
    makes the result a failure.
    """
    comps = [c for c in model.java.component() if [str(t) for t in c.geom().tags()]]
    if not comps:
        return {"success": False, "error": "No geometry found. Create and build a geometry first."}
    comp = comps[0]
    geom_tag = str(list(comp.geom().tags())[0])
    meshes = comp.mesh()
    seq = meshes.create(mesh_name) if mesh_name not in [str(t) for t in meshes.tags()] else comp.mesh(mesh_name)

    host = seq if parent is None else seq.feature(parent)
    existing = {str(t) for t in host.feature().tags()}
    prefix = feature_type[:4].lower()
    i = 1
    while f"{prefix}{i}" in existing:
        i += 1
    tag = f"{prefix}{i}"
    try:
        node = host.create(tag, feature_type)
    except Exception as e:
        return {"success": False, "error": f"Failed to add {feature_type}: {str(e).strip().splitlines()[0]}"}

    errors = []
    try:
        if entities == "remaining":
            node.selection().remaining()
        elif entities is not None:
            node.selection().geom(geom_tag, int(entity_dim))
            node.selection().set([int(e) for e in entities])
        for name, numbers in (selections or {}).items():
            node.selection(name).set([int(n) for n in numbers])
    except Exception as e:
        errors.append(f"Selection failed: {str(e).strip().splitlines()[0]}")

    rejected = {}
    for name, value in (properties or {}).items():
        try:
            node.set(name, str(value))
        except Exception as e:
            rejected[name] = str(e).strip().splitlines()[0]
    if rejected:
        errors.append(f"COMSOL rejected properties {sorted(rejected)}.")

    result = {
        "success": not errors,
        "mesh": mesh_name,
        "feature": {"tag": tag, "type": feature_type, "parent": parent},
        "properties": dict(properties or {}),
    }
    if rejected:
        result["rejected_properties"] = rejected
    if errors:
        host.feature().remove(tag)
        result["error"] = " ".join(errors) + f" Removed step {tag}. Fix the input and call again."
    return result


def register_mesh_tools(mcp: FastMCP) -> None:
    """Register mesh tools with the MCP server."""
    
    @mcp.tool()
    def mesh_list(model_name: Optional[str] = None) -> dict:
        """
        List all mesh sequences in a model.
        
        Args:
            model_name: Model name (default: current model)
        
        Returns:
            List of mesh sequence names
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            meshes = model.meshes()
            return {
                "success": True,
                "meshes": meshes,
                "count": len(meshes),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list meshes: {str(e)}"}
    
    @mcp.tool()
    def mesh_create(
        mesh_name: Optional[str] = None,
        model_name: Optional[str] = None,
        mesh_size: int = 3,
        max_element_size: Optional[float] = None,
        min_element_size: Optional[float] = None
    ) -> dict:
        """
        Create and build a physics-controlled mesh sequence.

        If no mesh sequence exists, one is created automatically using a
        physics-controlled free-tetrahedral mesh. If one already exists it
        is simply (re-)built.

        Args:
            mesh_name: Mesh sequence name (default: 'mesh1')
            model_name: Model name (default: current model)
            mesh_size: COMSOL predefined size 1 (extremely fine) – 9 (extremely coarse),
                       default 3 (fine)
            max_element_size: Maximum element size in meters. Use it to set
                       the element count across a narrow gap.
            min_element_size: Minimum element size in meters

        Returns:
            Mesh generation status
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }

        try:
            jm = model.java
            target = mesh_name or "mesh1"

            # Collect existing mesh sequence tags
            existing_tags = []
            for comp in jm.component():
                try:
                    existing_tags = [str(t) for t in comp.mesh().tags()]
                except Exception:
                    pass

            if target not in existing_tags:
                # Create a new physics-controlled free-tet mesh sequence
                comp = next(iter(jm.component()))
                mesh_seq = comp.mesh().create(target)
                mesh_seq.autoMeshSize(mesh_size)
                set_element_sizes(mesh_seq, max_element_size, min_element_size)
            else:
                # Re-run existing sequence
                comp = next(c for c in jm.component() if target in [str(t) for t in c.mesh().tags()])
                mesh_seq = comp.mesh(target)

            mesh_seq.run()
            if int(mesh_seq.getNumElem()) == 0:
                return {"success": False, "error": f"Mesh '{target}' built 0 elements."}

            return {
                "success": True,
                "mesh": target,
                "mesh_size_level": mesh_size,
                "message": f"Mesh '{target}' built successfully (size level {mesh_size}).",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to create mesh: {str(e)}"}
    
    @mcp.tool()
    def mesh_add_feature(
        feature_type: str,
        mesh_name: str = "mesh1",
        parent: Optional[str] = None,
        entity_dim: Optional[int] = None,
        entities: Optional[Union[str, Sequence[int]]] = None,
        selections: Optional[dict] = None,
        properties: Optional[dict] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a step to a mesh sequence for manual meshing of thin or long geometry.

        Example swept mesh: "FreeTri" on the source faces, with a "Size" step
        under it, then "Sweep" on the domains with selections {"sourceface": [...],
        "targetface": [...]} and a "Distribution" step under it (numelem),
        then "FreeTet" with entities "remaining". Call mesh_create to build.

        Args:
            feature_type: COMSOL mesh feature, e.g. "FreeTri", "Sweep", "FreeTet", "Size", "Distribution"
            mesh_name: Mesh sequence (created when it does not exist)
            parent: Tag of the step that holds this sub-step
            entity_dim: Dimension of entities: 3 domains, 2 faces
            entities: Entity numbers, or "remaining"
            selections: Named selections of the step, mapped to entity numbers
            properties: COMSOL properties, e.g. {"hmax": "0.8[mm]"} or {"numelem": 14}
            model_name: Model name (default: current model)

        Returns:
            The step tag, and any selection or property COMSOL rejected
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return add_mesh_feature(model, feature_type, mesh_name, parent, entity_dim, entities, selections, properties)

    @mcp.tool()
    def mesh_info(
        mesh_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Get information about a mesh.
        
        Args:
            mesh_name: Mesh sequence name (default: first mesh)
            model_name: Model name (default: current model)
        
        Returns:
            Element and vertex counts, and the minimum and mean element quality (skewness)
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            meshes = model.meshes()
            if not meshes:
                return {"success": False, "error": "No meshes defined in model."}
            
            target = mesh_name or meshes[0]
            if target not in meshes:
                return {"success": False, "error": f"Mesh not found: {target}"}
            
            mesh_node = model / "meshes" / target
            
            info = {
                "name": target,
            }
            
            info.update(mesh_counts(mesh_node.java))
            info.update(mesh_quality(mesh_node.java))
            
            try:
                children = [child.name() for child in mesh_node.children()]
                info["features"] = children
            except Exception:
                pass
            
            return {
                "success": True,
                "mesh": info,
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to get mesh info: {str(e)}"}
