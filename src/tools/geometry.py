"""Geometry tools for COMSOL MCP Server."""

from typing import Optional, Sequence
from mcp.server.fastmcp import FastMCP

from .session import session_manager


def _get_geometry_node(model, geometry_name: Optional[str], component_name: str = "comp1"):
    """Helper to get geometry node via Java API.

    Returns:
        tuple: (geom_node, error_message) - geom_node is None if error
    """
    jm = model.java

    try:
        comp = jm.component(component_name)
        if comp is None:
            return None, f"Component '{component_name}' not found."

        geom_list = comp.geom()
        tags = list(geom_list.tags())

        if not tags:
            return None, "No geometry sequences found. Create one first with geometry_create."

        if geometry_name:
            # Match by tag first, then fall back to first geometry
            tag = tags[0] if geometry_name not in tags else geometry_name
        else:
            tag = tags[0]

        return geom_list.get(tag), None
    except Exception as e:
        return None, f"Failed to get geometry: {str(e)}"


def _count_features(geom_node) -> int:
    """Safely count features in a geometry node."""
    try:
        return len(list(geom_node.feature().tags()))
    except Exception:
        return 0


def add_feature(model, feature_type: str, properties: Optional[dict] = None, feature_name: Optional[str] = None,
                geometry_name: Optional[str] = None, component_name: str = "comp1") -> dict:
    """Add any geometry feature through the Java API and set its properties.

    Numbers go as strings and a list of lists goes as a string matrix, because COMSOL has no set
    overload for a Python int. A property that COMSOL rejects is reported, not dropped.
    """
    geom, error = _get_geometry_node(model, geometry_name, component_name)
    if error:
        return {"success": False, "error": error}
    existing = [str(t) for t in geom.feature().tags()]
    prefix = feature_type[:3].lower()
    tag = feature_name or next(f"{prefix}{i}" for i in range(1, len(existing) + 2) if f"{prefix}{i}" not in existing)
    feature = geom.feature().create(tag, feature_type)
    rejected = {}
    for name, value in (properties or {}).items():
        if isinstance(value, (list, tuple)):
            value = [[str(v) for v in row] if isinstance(row, (list, tuple)) else str(row) for row in value]
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        try:
            feature.set(name, value)
        except Exception as e:
            text = str(e).strip()
            rejected[name] = text.splitlines()[-1].strip() if text else type(e).__name__
    result = {"success": not rejected, "feature": {"name": tag, "type": feature_type, "properties": sorted(properties or {})}}
    if rejected:
        result["rejected_properties"] = rejected
        result["error"] = f"COMSOL rejected properties {sorted(rejected)}."
    return result


def register_geometry_tools(mcp: FastMCP) -> None:
    """Register geometry tools with the MCP server."""
    
    @mcp.tool()
    def geometry_list(model_name: Optional[str] = None) -> dict:
        """
        List all geometry sequences in a model.
        
        Args:
            model_name: Model name (default: current model)
        
        Returns:
            List of geometry sequence names
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geometries = model.geometries()
            return {
                "success": True,
                "geometries": geometries,
                "count": len(geometries),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list geometries: {str(e)}"}
    
    @mcp.tool()
    def geometry_create(
        geometry_name: Optional[str] = None,
        space_dimension: int = 3,
        component_name: str = "comp1",
        model_name: Optional[str] = None
    ) -> dict:
        """
        Create a new geometry sequence in the model's component.
        
        IMPORTANT: A component must exist first. Use model_create_component if needed.
        
        Args:
            geometry_name: Name for the geometry sequence (default: 'geom1')
            space_dimension: Space dimension - 2 for 2D, 3 for 3D (default: 3)
            component_name: Component name (default: 'comp1')
            model_name: Model name (default: current model)
        
        Returns:
            Created geometry info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            jm = model.java
            
            geom_name = geometry_name or "geom1"
            
            comp = jm.component(component_name)
            if comp is None:
                return {
                    "success": False,
                    "error": f"Component '{component_name}' not found. Create it first with model_create_component."
                }
            
            geom = comp.geom().create(geom_name, space_dimension)
            
            return {
                "success": True,
                "geometry": geom_name,
                "component": component_name,
                "space_dimension": space_dimension,
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to create geometry: {str(e)}"}
    
    @mcp.tool()
    def geometry_add_feature(
        feature_type: str,
        properties: Optional[dict] = None,
        geometry_name: Optional[str] = None,
        feature_name: Optional[str] = None,
        component_name: str = "comp1",
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add any geometry feature to a geometry sequence and set its properties.

        Use it for features that have no tool of their own, for example a Hexahedron from 8 corner points
        (feature_type="Hexahedron", properties={"p": [[x1..x8], [y1..y8], [z1..z8]]}), a Cone, or a Polygon.

        Args:
            feature_type: COMSOL feature type, for example Hexahedron, Cone, Polygon, Union, Difference
            properties: COMSOL property names mapped to values. Numbers and number lists are sent as strings.
            geometry_name: Geometry sequence tag (default: first geometry)
            feature_name: Feature tag (default: first three letters of the type and a free number)
            component_name: Component tag (default: 'comp1')
            model_name: Model name (default: current model)

        Returns:
            The feature tag and type. Any property COMSOL rejected is listed, and success is then false.
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        try:
            return add_feature(model, feature_type, properties, feature_name, geometry_name, component_name)
        except Exception as e:
            return {"success": False, "error": f"Failed to add geometry feature: {str(e)}"}

    @mcp.tool()
    def geometry_add_block(
        position: Sequence[float] = (0, 0, 0),
        size: Sequence[float] = (1, 1, 1),
        geometry_name: Optional[str] = None,
        component_name: str = "comp1",
        feature_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a block (rectangular cuboid) to the geometry.
        
        Args:
            position: Base position [x, y, z] in meters (default: origin)
            size: Dimensions [width, depth, height] in meters (default: 1m cube)
            geometry_name: Geometry sequence name (default: first geometry)
            component_name: Component name (default: 'comp1')
            feature_name: Feature name (auto-generated if None)
            model_name: Model name (default: current model)
        
        Returns:
            Created block info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geom, error = _get_geometry_node(model, geometry_name, component_name)
            if error:
                return {"success": False, "error": error}
            
            feat_name = feature_name or f"blk{_count_features(geom)+1}"
            block = geom.feature().create(feat_name, "Block")
            
            block.set("pos", [str(p) for p in position])
            block.set("size", [str(s) for s in size])
            
            return {
                "success": True,
                "feature": {
                    "name": feat_name,
                    "type": "Block",
                    "position": list(position),
                    "size": list(size),
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add block: {str(e)}"}
    
    @mcp.tool()
    def geometry_add_cylinder(
        position: Sequence[float] = (0, 0, 0),
        radius: float = 0.5,
        height: float = 1.0,
        geometry_name: Optional[str] = None,
        component_name: str = "comp1",
        feature_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a cylinder to the geometry.
        
        Args:
            position: Center of base [x, y, z] in meters
            radius: Radius in meters (default: 0.5)
            height: Height in meters (default: 1.0)
            geometry_name: Geometry sequence name (default: first geometry)
            component_name: Component name (default: 'comp1')
            feature_name: Feature name (auto-generated if None)
            model_name: Model name (default: current model)
        
        Returns:
            Created cylinder info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geom, error = _get_geometry_node(model, geometry_name, component_name)
            if error:
                return {"success": False, "error": error}
            
            feat_name = feature_name or f"cyl{_count_features(geom)+1}"
            cyl = geom.feature().create(feat_name, "Cylinder")
            
            cyl.set("pos", [str(p) for p in position])
            cyl.set("r", str(radius))
            cyl.set("h", str(height))
            
            return {
                "success": True,
                "feature": {
                    "name": feat_name,
                    "type": "Cylinder",
                    "position": list(position),
                    "radius": radius,
                    "height": height,
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add cylinder: {str(e)}"}
    
    @mcp.tool()
    def geometry_add_sphere(
        position: Sequence[float] = (0, 0, 0),
        radius: float = 0.5,
        geometry_name: Optional[str] = None,
        component_name: str = "comp1",
        feature_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a sphere to the geometry.
        
        Args:
            position: Center [x, y, z] in meters
            radius: Radius in meters (default: 0.5)
            geometry_name: Geometry sequence name (default: first geometry)
            component_name: Component name (default: 'comp1')
            feature_name: Feature name (auto-generated if None)
            model_name: Model name (default: current model)
        
        Returns:
            Created sphere info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geom, error = _get_geometry_node(model, geometry_name, component_name)
            if error:
                return {"success": False, "error": error}
            
            feat_name = feature_name or f"sph{_count_features(geom)+1}"
            sphere = geom.feature().create(feat_name, "Sphere")
            
            sphere.set("pos", [str(p) for p in position])
            sphere.set("r", str(radius))
            
            return {
                "success": True,
                "feature": {
                    "name": feat_name,
                    "type": "Sphere",
                    "position": list(position),
                    "radius": radius,
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add sphere: {str(e)}"}
    
    @mcp.tool()
    def geometry_add_rectangle(
        position: Sequence[float] = (0, 0),
        size: Sequence[float] = (1, 1),
        geometry_name: Optional[str] = None,
        component_name: str = "comp1",
        feature_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a rectangle to a 2D geometry or work plane.
        
        Args:
            position: Base position [x, y] in meters
            size: Dimensions [width, height] in meters
            geometry_name: Geometry sequence name (default: first geometry)
            component_name: Component name (default: 'comp1')
            feature_name: Feature name (auto-generated if None)
            model_name: Model name (default: current model)
        
        Returns:
            Created rectangle info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geom, error = _get_geometry_node(model, geometry_name, component_name)
            if error:
                return {"success": False, "error": error}
            
            feat_name = feature_name or f"r{_count_features(geom)+1}"
            rect = geom.feature().create(feat_name, "Rectangle")
            
            rect.set("pos", [str(p) for p in position])
            rect.set("size", [str(s) for s in size])
            
            return {
                "success": True,
                "feature": {
                    "name": feat_name,
                    "type": "Rectangle",
                    "position": list(position),
                    "size": list(size),
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add rectangle: {str(e)}"}
    
    @mcp.tool()
    def geometry_add_circle(
        position: Sequence[float] = (0, 0),
        radius: float = 0.5,
        geometry_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a circle to a 2D geometry or work plane.
        
        Args:
            position: Center [x, y] in meters
            radius: Radius in meters (default: 0.5)
            geometry_name: Geometry sequence name
            model_name: Model name (default: current model)
        
        Returns:
            Created circle info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geometries = model.geometries()
            if not geometries:
                return {"success": False, "error": "No geometry sequences found."}
            
            target_geom = geometry_name or geometries[0]
            geom_node = model / "geometries" / target_geom
            circle_node = geom_node.create("Circle")
            
            if len(position) == 2:
                circle_node.property("pos", list(position))
            circle_node.property("r", radius)
            
            return {
                "success": True,
                "feature": {
                    "name": circle_node.name() if hasattr(circle_node, 'name') else "Circle",
                    "type": "Circle",
                    "geometry": target_geom,
                    "position": list(position),
                    "radius": radius,
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add circle: {str(e)}"}
    
    @mcp.tool()
    def geometry_boolean_union(
        input_objects: Sequence[str],
        geometry_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Create a boolean union of geometry objects.
        
        Args:
            input_objects: Names of objects to unite
            geometry_name: Geometry sequence name
            model_name: Model name (default: current model)
        
        Returns:
            Created union operation info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geometries = model.geometries()
            if not geometries:
                return {"success": False, "error": "No geometry sequences found."}
            
            target_geom = geometry_name or geometries[0]
            geom_node = model / "geometries" / target_geom
            union_node = geom_node.create("Union")
            union_node.property("input", list(input_objects))
            
            return {
                "success": True,
                "feature": {
                    "name": union_node.name() if hasattr(union_node, 'name') else "Union",
                    "type": "Union",
                    "geometry": target_geom,
                    "input_objects": list(input_objects),
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to create union: {str(e)}"}
    
    @mcp.tool()
    def geometry_boolean_difference(
        input_object: str,
        objects_to_subtract: Sequence[str],
        geometry_name: Optional[str] = None,
        component_name: str = "comp1",
        feature_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Create a boolean difference (subtract objects from another).
        
        Args:
            input_object: Object to subtract from (e.g., 'blk1')
            objects_to_subtract: Objects to remove (e.g., ['cyl1'])
            geometry_name: Geometry sequence name (default: first geometry)
            component_name: Component name (default: 'comp1')
            feature_name: Feature name (auto-generated if None)
            model_name: Model name (default: current model)
        
        Returns:
            Created difference operation info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geom, error = _get_geometry_node(model, geometry_name, component_name)
            if error:
                return {"success": False, "error": error}
            
            feat_name = feature_name or f"dif{_count_features(geom)+1}"
            diff = geom.feature().create(feat_name, "Difference")
            
            diff.selection("input").set([input_object])
            diff.selection("input2").set(list(objects_to_subtract))
            
            return {
                "success": True,
                "feature": {
                    "name": feat_name,
                    "type": "Difference",
                    "input_object": input_object,
                    "subtracted": list(objects_to_subtract),
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to create difference: {str(e)}"}
    
    @mcp.tool()
    def geometry_import(
        file_path: str,
        geometry_name: Optional[str] = None,
        import_type: str = "CAD",
        model_name: Optional[str] = None
    ) -> dict:
        """
        Import geometry from a CAD file.
        
        Supported formats: STEP, IGES, STL, NASTRAN, etc.
        
        Args:
            file_path: Path to the CAD file
            geometry_name: Geometry sequence name
            import_type: Import type (CAD, mesh, etc.)
            model_name: Model name (default: current model)
        
        Returns:
            Import operation info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geometries = model.geometries()
            if not geometries:
                return {"success": False, "error": "No geometry sequences found."}
            
            target_geom = geometry_name or geometries[0]
            geom_node = model / "geometries" / target_geom
            import_node = geom_node.create("Import")
            
            model.import_(import_node, file_path)
            
            return {
                "success": True,
                "feature": {
                    "name": import_node.name() if hasattr(import_node, 'name') else "Import",
                    "type": "Import",
                    "geometry": target_geom,
                    "file": file_path,
                    "import_type": import_type,
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to import geometry: {str(e)}"}
    
    @mcp.tool()
    def geometry_build(
        geometry_name: Optional[str] = None,
        component_name: str = "comp1",
        model_name: Optional[str] = None
    ) -> dict:
        """
        Build the geometry sequence to generate the actual geometry.
        
        This must be called after adding/modifying geometry features.
        
        Args:
            geometry_name: Geometry sequence name (default: build all)
            component_name: Component name (default: 'comp1')
            model_name: Model name (default: current model)
        
        Returns:
            Build status
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geom, error = _get_geometry_node(model, geometry_name, component_name)
            if error:
                return {"success": False, "error": error}
            
            geom.run()
            
            return {
                "success": True,
                "geometry": geometry_name or "first",
                "message": "Geometry built successfully.",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to build geometry: {str(e)}"}
    
    @mcp.tool()
    def geometry_list_features(
        geometry_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        List all features in a geometry sequence.
        
        Args:
            geometry_name: Geometry sequence name (default: first geometry)
            model_name: Model name (default: current model)
        
        Returns:
            List of geometry features with their types
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            geometries = model.geometries()
            if not geometries:
                return {"success": False, "error": "No geometry sequences found."}
            
            target_geom = geometry_name or geometries[0]
            if target_geom not in geometries:
                return {"success": False, "error": f"Geometry not found: {target_geom}"}
            
            geom_node = model / "geometries" / target_geom
            features = []
            
            for child in geom_node.children():
                feat_info = {"name": child.name()}
                try:
                    feat_info["type"] = child.type() if hasattr(child, 'type') else "unknown"
                except Exception:
                    pass
                features.append(feat_info)
            
            return {
                "success": True,
                "geometry": target_geom,
                "features": features,
                "count": len(features),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list features: {str(e)}"}
