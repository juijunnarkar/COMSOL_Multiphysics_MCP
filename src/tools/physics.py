"""Physics tools for COMSOL MCP Server."""

from typing import Optional, Sequence
from mcp.server.fastmcp import FastMCP

from .session import session_manager


PHYSICS_INTERFACES = {
    "AC/DC": {
        "electrostatic": "Electrostatics (es)",
        "electric_currents": "Electric Currents (ec)",
        "magnetic_fields": "Magnetic Fields (mf)",
        "electromagnetic_waves": "Electromagnetic Waves (emw)",
    },
    "Structural": {
        "solid_mechanics": "Solid Mechanics (solid)",
        "shell": "Shell (shell)",
        "beam": "Beam (beam)",
        "membrane": "Membrane (memb)",
    },
    "Heat Transfer": {
        "heat_transfer": "Heat Transfer in Solids (ht)",
        "conjugate_ht": "Conjugate Heat Transfer (cht)",
        "radiation": "Radiation (rad)",
    },
    "Fluid Flow": {
        "laminar_flow": "Laminar Flow (spf)",
        "turbulent_flow": "Turbulent Flow (spf)",
        "creeping_flow": "Creeping Flow (brinkman)",
    },
    "Acoustics": {
        "pressure_acoustics": "Pressure Acoustics (acpr)",
        "thermoacoustics": "Thermoacoustics (ta)",
    },
    "Chemical": {
        "transport_diluted": "Transport of Diluted Species (tds)",
        "reaction_engineering": "Reaction Engineering (re)",
    },
    "Optics": {
        "ray_optics": "Geometrical Optics (gop)",
        "wave_optics": "Wave Optics (ewfd)",
    },
    "Multiphysics": {
        "thermal_stress": "Thermal Stress (ts)",
        "fluid_structure": "Fluid-Structure Interaction (fsi)",
        "electromechanical": "Electromechanical Forces",
        "joule_heating": "Joule Heating (jh)",
    },
}


def _selection_entities(sel, dims: Sequence[int]) -> list[int]:
    """Read back the entities of a selection, trying each dimension in order."""
    for dim in dims:
        try:
            ents = [int(i) for i in list(sel.entities(dim))]
            if ents:
                return ents
        except Exception:
            continue
    return []


def assign_material(
    model,
    material_name: str,
    domain_selection: Optional[Sequence[int]] = None,
    physics_name: Optional[str] = None,
) -> dict:
    """
    Set the domain selection of a component material and read it back.

    The material is found by tag ("mat1") or label ("Aluminum"). Materials
    belong to a component, not to a physics interface, so physics_name only
    picks the component when more than one component has the material.
    """
    jm = model.java

    if domain_selection is not None:
        try:
            domains = [int(d) for d in domain_selection]
        except (TypeError, ValueError):
            return {"success": False, "error": f"Domain numbers must be integers: {domain_selection}"}
        if not domains or any(d < 1 for d in domains):
            return {"success": False, "error": f"Domain numbers must be 1 or greater: {domain_selection}"}
    else:
        domains = None

    components = list(jm.component())
    if physics_name is not None:
        components = [c for c in components if physics_name in [str(t) for t in c.physics().tags()]]
        if not components:
            return {"success": False, "error": f"Physics interface not found: {physics_name}"}

    matches = []
    available = []
    for comp in components:
        for tag in [str(t) for t in comp.material().tags()]:
            label = str(comp.material(tag).label())
            available.append(f"{label} ({tag}, {comp.tag()})")
            if material_name in (tag, label):
                matches.append((comp, tag, label))

    if not matches:
        return {
            "success": False,
            "error": f"Material not found in any component: {material_name}",
            "available": available,
            "hint": "A global material needs a Material Link in the component before it can be assigned.",
        }
    if len(matches) > 1:
        return {
            "success": False,
            "error": f"Material '{material_name}' matches more than one node.",
            "matches": [f"{label} ({tag}, {comp.tag()})" for comp, tag, label in matches],
            "hint": "Pass the material tag, or pass physics_name to pick the component.",
        }

    comp, tag, label = matches[0]
    sel = comp.material(tag).selection()
    try:
        if domains is None:
            sel.all()
        else:
            sel.set(domains)
    except Exception as e:
        return {"success": False, "error": f"Failed to set the selection of {tag}: {e}"}

    # Read the selection back so the result reports what COMSOL holds, not what was asked.
    dims = []
    try:
        geom_tags = [str(t) for t in comp.geom().tags()]
        if geom_tags:
            dims.append(int(comp.geom(geom_tags[0]).getSDim()))
    except Exception:
        pass
    dims += [d for d in (3, 2, 1) if d not in dims]
    applied = _selection_entities(sel, dims)

    missing = sorted(set(domains) - set(applied)) if domains is not None else []
    result = {
        "success": bool(applied) and not missing,
        "material": {"tag": tag, "label": label, "component": str(comp.tag())},
        "requested_domains": domains if domains is not None else "all",
        "applied_domains": applied,
    }
    if not applied:
        result["error"] = "The material selection is empty after assignment."
    elif missing:
        result["error"] = f"COMSOL did not apply domains {missing}. Check the domain numbers."
    return result


# Short names and COMSOL type names, mapped to (COMSOL type, default tag).
PHYSICS_TYPES = {
    "es": ("Electrostatics", "es"),
    "electrostatics": ("Electrostatics", "es"),
    "ec": ("ConductiveMedia", "ec"),
    "electriccurrents": ("ConductiveMedia", "ec"),
    "conductivemedia": ("ConductiveMedia", "ec"),
    "solid": ("SolidMechanics", "solid"),
    "solidmechanics": ("SolidMechanics", "solid"),
    "ht": ("HeatTransfer", "ht"),
    "heattransfer": ("HeatTransfer", "ht"),
    "spf": ("LaminarFlow", "spf"),
    "laminarflow": ("LaminarFlow", "spf"),
}


def _tags(node_list) -> list[str]:
    return [str(t) for t in node_list.tags()]


def _physics_tag(node_list, prefix: str) -> str:
    """COMSOL style for physics tags: ht, then ht2, ht3, ..."""
    existing = set(_tags(node_list))
    if prefix not in existing:
        return prefix
    i = 2
    while f"{prefix}{i}" in existing:
        i += 1
    return f"{prefix}{i}"


def _numbered_tag(node_list, prefix: str) -> str:
    """COMSOL style for feature and material tags: temp1, temp2, ..."""
    existing = set(_tags(node_list))
    i = 1
    while f"{prefix}{i}" in existing:
        i += 1
    return f"{prefix}{i}"


def _first_error_line(e: Exception) -> str:
    """Return the first COMSOL message line (after "Messages:"), or the first line of the error."""
    lines = [ln.strip() for ln in str(e).splitlines() if ln.strip()]
    if "Messages:" in lines:
        after = lines[lines.index("Messages:") + 1:]
        if after:
            return after[0]
    return lines[0] if lines else repr(e)


def _component_with_geometry(jm, component_name: Optional[str] = None):
    """Return (component, geometry tag) for the named component, or the first one with a geometry."""
    for comp in jm.component():
        if component_name is not None and str(comp.tag()) != component_name:
            continue
        geom_tags = _tags(comp.geom())
        if geom_tags:
            return comp, geom_tags[0]
    return None, None


def find_physics(model, physics_name: str):
    """
    Find a physics interface by tag ("ht") or label ("Heat Transfer in Solids").

    Returns (component, physics node, None), or (None, None, error dict).
    """
    available = []
    for comp in model.java.component():
        for tag in _tags(comp.physics()):
            phys = comp.physics(tag)
            label = str(phys.label())
            available.append(f"{label} ({tag})")
            if physics_name in (tag, label):
                return comp, phys, None
    return None, None, {
        "success": False,
        "error": f"Physics interface not found: {physics_name}",
        "available": available,
    }


def add_physics(
    model,
    physics_type: str,
    component_name: Optional[str] = None,
    selection_name: Optional[str] = None,
) -> dict:
    """
    Add a physics interface on the geometry of a component.

    COMSOL needs the geometry tag when it creates a physics interface. Without
    it, the interface has dimension 0D and creation fails.
    """
    comsol_type, prefix = PHYSICS_TYPES.get(
        physics_type.lower(), (physics_type, physics_type.lower()[:4] or "phys")
    )
    comp, geom_tag = _component_with_geometry(model.java, component_name)
    if comp is None:
        where = f"component '{component_name}'" if component_name else "any component"
        return {"success": False, "error": f"No geometry found in {where}. Create and build a geometry first."}

    plist = comp.physics()
    tag = _physics_tag(plist, prefix)
    try:
        phys = plist.create(tag, comsol_type, geom_tag)
    except Exception as e:
        return {"success": False, "error": f"Failed to add {comsol_type}: {_first_error_line(e)}"}

    result = {
        "success": True,
        "physics": {
            "tag": tag,
            "type": comsol_type,
            "label": str(phys.label()),
            "component": str(comp.tag()),
            "geometry": geom_tag,
        },
    }
    if selection_name:
        try:
            phys.selection().named(selection_name)
            result["physics"]["selection"] = selection_name
        except Exception as e:
            result["success"] = False
            result["error"] = f"Added {tag}, but could not apply selection '{selection_name}': {_first_error_line(e)}"
    return result


def add_boundary_feature(
    model,
    physics_name: str,
    feature_type: str,
    boundaries: Sequence[int],
    properties: Optional[dict] = None,
    label: Optional[str] = None,
) -> dict:
    """
    Add a boundary feature (for example TemperatureBoundary) to a physics interface.

    The feature is created at the boundary dimension (space dimension - 1).
    The result reports the boundaries that COMSOL applied and every property
    that COMSOL rejected. A rejected property or a dropped boundary makes the
    result a failure.
    """
    try:
        requested = [int(b) for b in boundaries]
    except (TypeError, ValueError):
        return {"success": False, "error": f"Boundary numbers must be integers: {boundaries}"}
    if not requested or any(b < 1 for b in requested):
        return {"success": False, "error": f"Boundary numbers must be 1 or greater: {boundaries}"}

    comp, phys, error = find_physics(model, physics_name)
    if error:
        return error

    try:
        sdim = int(comp.geom(_tags(comp.geom())[0]).getSDim())
    except Exception:
        sdim = 3
    bdim = sdim - 1

    tag = _numbered_tag(phys.feature(), feature_type[:4].lower())
    try:
        feat = phys.create(tag, feature_type, bdim)
    except Exception as e:
        return {"success": False, "error": f"Failed to create {feature_type} in {physics_name}: {_first_error_line(e)}"}
    if label:
        feat.label(label)

    try:
        feat.selection().set(requested)
        applied = [int(i) for i in list(feat.selection().entities(bdim))]
    except Exception as e:
        return {"success": False, "error": f"Created {tag}, but could not set boundaries {requested}: {_first_error_line(e)}"}

    rejected = {}
    for name, value in (properties or {}).items():
        try:
            feat.set(name, str(value))
        except Exception as e:
            rejected[name] = _first_error_line(e)

    missing = sorted(set(requested) - set(applied))
    result = {
        "success": bool(applied) and not missing and not rejected,
        "physics": str(phys.tag()),
        "feature": {"tag": tag, "type": feature_type, "dimension": bdim},
        "requested_boundaries": requested,
        "applied_boundaries": applied,
        "properties": dict(properties or {}),
    }
    errors = []
    if not applied:
        errors.append("The feature selection is empty.")
    elif missing:
        errors.append(f"COMSOL did not apply boundaries {missing}. Check the numbers with geometry_get_boundaries.")
    if rejected:
        result["rejected_properties"] = rejected
        errors.append(f"COMSOL rejected properties {sorted(rejected)}.")
    if errors:
        # Do not leave a half-configured feature in the model: it would still act on the solution.
        try:
            phys.feature().remove(tag)
            errors.append(f"Removed feature {tag}. Fix the input and call again.")
            result["feature"]["removed"] = True
        except Exception as e:
            errors.append(f"Could not remove feature {tag}: {_first_error_line(e)}")
        result["error"] = " ".join(errors)
    return result


def _java_matrix(rows):
    """Convert [[u, v], ...] to double[][] when the JVM runs. Tests keep the list."""
    try:
        import jpype
        if jpype.isJVMStarted():
            return jpype.JArray(jpype.JDouble, 2)(rows)
    except Exception:
        pass
    return rows


def boundary_info(model, geometry_name: Optional[str] = None, samples: int = 5) -> dict:
    """
    List the boundaries of a geometry with a center point, normal, and bounding box.

    The values come from sampling each face on a samples x samples grid of
    its parameters. The bounding box is exact for flat faces with straight
    edges and approximate for curved faces. Only 3D geometries get these values.
    """
    comp = None
    for c in model.java.component():
        tags = _tags(c.geom())
        if tags and (geometry_name is None or geometry_name in tags):
            comp = c
            geometry_name = geometry_name or tags[0]
            break
    if comp is None:
        return {"success": False, "error": f"Geometry not found: {geometry_name or 'no geometry in the model'}"}

    geom = comp.geom(geometry_name)
    try:
        geom.run()
        sdim = int(geom.getSDim())
        n_boundaries = int(geom.getNBoundaries())
        n_domains = int(geom.getNDomains())
    except Exception as e:
        return {"success": False, "error": f"Could not read geometry {geometry_name}: {_first_error_line(e)}"}

    n = max(2, int(samples))
    boundaries = []
    for face in range(1, n_boundaries + 1):
        info = {"boundary_number": face}
        if sdim == 3:
            try:
                u0, u1, v0, v1 = [float(x) for x in geom.faceParamRange(face)]
                grid = [[u0 + (u1 - u0) * i / (n - 1), v0 + (v1 - v0) * j / (n - 1)]
                        for i in range(n) for j in range(n)]
                mid = [[(u0 + u1) / 2, (v0 + v1) / 2]]
                pts = [[float(c) for c in p] for p in geom.faceX(face, _java_matrix(grid))]
                center = [float(c) for c in geom.faceX(face, _java_matrix(mid))[0]]
                normal = [float(c) for c in geom.faceNormal(face, _java_matrix(mid))[0]]
                info["center"] = [round(c, 12) + 0.0 for c in center]
                info["normal"] = [round(c, 6) + 0.0 for c in normal]
                info["bounding_box"] = {
                    "min": [round(min(p[k] for p in pts), 12) + 0.0 for k in range(3)],
                    "max": [round(max(p[k] for p in pts), 12) + 0.0 for k in range(3)],
                }
            except Exception as e:
                info["error"] = f"Could not sample face: {_first_error_line(e)}"
        boundaries.append(info)

    return {
        "success": True,
        "geometry": geometry_name,
        "component": str(comp.tag()),
        "space_dimension": sdim,
        "total_boundaries": n_boundaries,
        "total_domains": n_domains,
        "boundaries": boundaries,
        "hint": "Pick faces by center and normal. Pass boundary_number to physics_configure_boundary.",
    }


# Friendly names for material properties, mapped to COMSOL names in the "def" property group.
MATERIAL_PROPERTIES = {
    "thermal_conductivity": "thermalconductivity",
    "density": "density",
    "heat_capacity": "heatcapacity",
    "youngs_modulus": "youngsmodulus",
    "poissons_ratio": "poissonsratio",
    "thermal_expansion": "thermalexpansioncoefficient",
    "electric_conductivity": "electricconductivity",
    "relative_permittivity": "relpermittivity",
    "dynamic_viscosity": "dynamicviscosity",
    "ratio_of_specific_heats": "ratioofspecificheat",
}


def create_material(
    model,
    label: str,
    properties: dict,
    domain_selection: Optional[Sequence[int]] = None,
    component_name: Optional[str] = None,
) -> dict:
    """
    Create a material in a component, set its properties, and assign it to domains.

    Property names can be friendly names (thermal_conductivity) or COMSOL
    names (thermalconductivity). Values are strings with units, for example
    "200[W/(m*K)]". A property that COMSOL rejects makes the result a failure.
    """
    if not properties:
        return {"success": False, "error": "Give at least one property, for example {'thermal_conductivity': '200[W/(m*K)]'}."}

    comps = [c for c in model.java.component() if component_name is None or str(c.tag()) == component_name]
    if not comps:
        return {"success": False, "error": f"Component not found: {component_name or 'no component in the model'}"}
    comp = comps[0]

    mlist = comp.material()
    tag = _numbered_tag(mlist, "mat")
    try:
        mat = mlist.create(tag, "Common")
        mat.label(label)
        group = mat.propertyGroup("def")
    except Exception as e:
        return {"success": False, "error": f"Failed to create material: {_first_error_line(e)}"}

    applied, rejected = {}, {}
    for name, value in properties.items():
        comsol_name = MATERIAL_PROPERTIES.get(name, name)
        try:
            group.set(comsol_name, [str(value)])
            applied[comsol_name] = str(value)
        except Exception as e:
            rejected[name] = _first_error_line(e)

    assignment = assign_material(model, tag, domain_selection)
    result = {
        "success": not rejected and bool(assignment.get("success")),
        "material": {"tag": tag, "label": label, "component": str(comp.tag())},
        "properties": applied,
        "requested_domains": assignment.get("requested_domains"),
        "applied_domains": assignment.get("applied_domains", []),
    }
    errors = []
    if rejected:
        result["rejected_properties"] = rejected
        errors.append(f"COMSOL rejected properties {sorted(rejected)}.")
        try:
            mlist.remove(tag)
            errors.append(f"Removed material {tag}. Fix the input and call again.")
            result["material"]["removed"] = True
        except Exception as e:
            errors.append(f"Could not remove material {tag}: {_first_error_line(e)}")
    if not assignment.get("success"):
        errors.append(assignment.get("error", "Domain assignment failed."))
    if errors:
        result["error"] = " ".join(errors)
    return result


def register_physics_tools(mcp: FastMCP) -> None:
    """Register physics tools with the MCP server."""
    
    @mcp.tool()
    def physics_list(model_name: Optional[str] = None) -> dict:
        """
        List all physics interfaces defined in a model.
        
        Args:
            model_name: Model name (default: current model)
        
        Returns:
            List of physics interface names
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            physics = model.physics()
            multiphysics = model.multiphysics()
            
            return {
                "success": True,
                "physics": physics,
                "multiphysics": multiphysics,
                "physics_count": len(physics),
                "multiphysics_count": len(multiphysics),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list physics: {str(e)}"}
    
    @mcp.tool()
    def physics_get_available() -> dict:
        """
        Get a list of available physics interfaces organized by category.
        
        Returns:
            Dictionary of physics categories and their interfaces
        """
        return {
            "success": True,
            "interfaces": PHYSICS_INTERFACES,
            "note": "Interface identifiers (in parentheses) are used when adding physics.",
        }
    
    @mcp.tool()
    def physics_add(
        physics_type: str,
        component_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a physics interface to the model, on the component geometry.

        Common physics types:
        - "Electrostatics" or "es": Electrostatic field analysis
        - "ElectricCurrents" or "ec": Electric current conduction
        - "SolidMechanics" or "solid": Structural stress analysis
        - "HeatTransfer" or "ht": Heat transfer in solids
        - "LaminarFlow" or "spf": Fluid dynamics
        Other COMSOL type names (for example "TransportDilutedSpecies") pass through unchanged.

        Args:
            physics_type: Type identifier (e.g., "Electrostatics", "es")
            component_name: Component to add physics to (default: first component with a geometry)
            model_name: Model name (default: current model)

        Returns:
            The physics tag, type, label, component, and geometry, or an error
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return add_physics(model, physics_type, component_name=component_name)
    
    @mcp.tool()
    def physics_add_electrostatics(
        domain_selection: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add Electrostatics physics for electric field analysis.

        Args:
            domain_selection: Name of a domain selection (default: all domains)
            model_name: Model name (default: current model)

        Returns:
            The physics tag, type, label, component, and geometry, or an error
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return add_physics(model, "Electrostatics", selection_name=domain_selection)
    
    @mcp.tool()
    def physics_add_solid_mechanics(
        domain_selection: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add Solid Mechanics physics for structural analysis.

        Args:
            domain_selection: Name of a domain selection (default: all domains)
            model_name: Model name (default: current model)

        Returns:
            The physics tag, type, label, component, and geometry, or an error
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return add_physics(model, "SolidMechanics", selection_name=domain_selection)
    
    @mcp.tool()
    def physics_add_heat_transfer(
        domain_selection: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add Heat Transfer in Solids physics for thermal analysis.

        Args:
            domain_selection: Name of a domain selection (default: all domains)
            model_name: Model name (default: current model)

        Returns:
            The physics tag, type, label, component, and geometry, or an error
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return add_physics(model, "HeatTransfer", selection_name=domain_selection)
    
    @mcp.tool()
    def physics_add_laminar_flow(
        domain_selection: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add Laminar Flow physics for fluid dynamics.

        Args:
            domain_selection: Name of a domain selection (default: all domains)
            model_name: Model name (default: current model)

        Returns:
            The physics tag, type, label, component, and geometry, or an error
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return add_physics(model, "LaminarFlow", selection_name=domain_selection)
    
    @mcp.tool()
    def physics_configure_boundary(
        physics_name: str,
        boundary_condition: str,
        boundary_selection: Sequence[int],
        properties: Optional[dict] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a boundary condition to a physics interface.

        Use COMSOL feature type names:
        - Heat Transfer (ht): "TemperatureBoundary" (T0), "HeatFluxBoundary" (q0, or
          HeatFluxType="ConvectiveHeatFlux" with h and Text), "ThermalInsulation"
        - Solid Mechanics (solid): "Fixed", "Roller", "BoundaryLoad" (FperArea)
        - Electrostatics (es): "Ground", "ElectricPotential" (V0), "SurfaceChargeDensity" (rhoqs)
        - Laminar Flow (spf): "InletBoundary" (U0in), "OutletBoundary" (p0), "WallBC"

        Args:
            physics_name: Physics tag (e.g. "ht") or label (e.g. "Heat Transfer in Solids")
            boundary_condition: COMSOL feature type (see above)
            boundary_selection: Boundary numbers (see geometry_get_boundaries)
            properties: Property names and values, e.g. {"T0": "400[K]"}
            model_name: Model name (default: current model)

        Returns:
            The feature tag, the boundaries COMSOL applied, and any property COMSOL rejected
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return add_boundary_feature(model, physics_name, boundary_condition, boundary_selection, properties)

    @mcp.tool()
    def material_create(
        label: str,
        properties: dict,
        domain_selection: Optional[Sequence[int]] = None,
        component_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Create a material with constant properties and assign it to domains.

        Property names: thermal_conductivity, density, heat_capacity,
        youngs_modulus, poissons_ratio, thermal_expansion, electric_conductivity,
        relative_permittivity, dynamic_viscosity, ratio_of_specific_heats, or any
        COMSOL property name in the "def" group. Give each value with its unit.

        Example: material_create("Aluminum 6063", {"thermal_conductivity": "200[W/(m*K)]",
        "density": "2700[kg/m^3]", "heat_capacity": "900[J/(kg*K)]"})

        Args:
            label: Material label
            properties: Property names and values with units
            domain_selection: Domain numbers (default: all domains)
            component_name: Component (default: first component)
            model_name: Model name (default: current model)

        Returns:
            The material tag, the properties set, the domains COMSOL applied, and any property COMSOL rejected
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return create_material(model, label, properties, domain_selection, component_name)
    
    @mcp.tool()
    def physics_set_material(
        material_name: str,
        domain_selection: Optional[Sequence[int]] = None,
        physics_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Assign a material to domains and report the domains COMSOL applied.

        Args:
            material_name: Material tag (e.g. "mat1") or label (e.g. "Aluminum")
            domain_selection: Domain numbers (default: all domains)
            physics_name: Physics tag (e.g. "ht"). Use it only to pick the
                component when the material exists in more than one component.
            model_name: Model name (default: current model)

        Returns:
            The material, the requested domains, and the applied domains.
            success is False when any requested domain was not applied.
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }

        try:
            return assign_material(model, material_name, domain_selection, physics_name)
        except Exception as e:
            return {"success": False, "error": f"Failed to set material: {str(e)}"}
    
    @mcp.tool()
    def multiphysics_add(
        coupling_type: str,
        physics_list: Sequence[str],
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add a multiphysics coupling between physics interfaces.
        
        Common coupling types:
        - "ThermalStress": Couples Heat Transfer and Solid Mechanics
        - "FluidStructureInteraction": Couples Fluid Flow and Solid Mechanics
        - "ElectromechanicalForces": Couples Electrostatics and Solid Mechanics
        - "JouleHeating": Couples Electric Currents and Heat Transfer
        
        Args:
            coupling_type: Type of multiphysics coupling
            physics_list: Names of physics interfaces to couple
            model_name: Model name (default: current model)
        
        Returns:
            Created coupling info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            coupling_node = model.create("multiphysics", coupling_type)
            
            return {
                "success": True,
                "coupling": {
                    "name": coupling_node.name() if hasattr(coupling_node, 'name') else coupling_type,
                    "type": coupling_type,
                    "physics": list(physics_list),
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add multiphysics: {str(e)}"}
    
    @mcp.tool()
    def physics_list_features(
        physics_name: str,
        model_name: Optional[str] = None
    ) -> dict:
        """
        List all features (boundary conditions, domain settings) in a physics interface.
        
        Args:
            physics_name: Name of the physics interface
            model_name: Model name (default: current model)
        
        Returns:
            List of physics features
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            comp, physics, error = find_physics(model, physics_name)
            if error:
                return error
            features = []
            for tag in _tags(physics.feature()):
                feat = physics.feature(tag)
                feat_info = {"tag": tag, "name": str(feat.label())}
                try:
                    feat_info["type"] = str(feat.getType())
                except Exception:
                    pass
                features.append(feat_info)
            
            return {
                "success": True,
                "physics": physics_name,
                "features": features,
                "count": len(features),
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to list features: {str(e)}"}
    
    @mcp.tool()
    def physics_remove(
        physics_name: str,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Remove a physics interface from the model.
        
        Args:
            physics_name: Name of the physics interface to remove
            model_name: Model name (default: current model)
        
        Returns:
            Removal confirmation
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            comp, physics, error = find_physics(model, physics_name)
            if error:
                return error
            comp.physics().remove(str(physics.tag()))
            
            return {
                "success": True,
                "removed": physics_name,
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to remove physics: {str(e)}"}
    
    @mcp.tool()
    def geometry_get_boundaries(
        geometry_name: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        List the boundaries of a geometry, with the center point, normal, and bounding box of each face.

        Use the center and normal to find a face (for example the face at x = 0
        has normal [-1, 0, 0]), then pass its boundary_number to
        physics_configure_boundary. Center, normal, and bounding box are given
        for 3D geometries only.

        Args:
            geometry_name: Geometry tag, e.g. "geom1" (default: first geometry)
            model_name: Model name (default: current model)

        Returns:
            Boundary count, domain count, and one entry for each boundary
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        return boundary_info(model, geometry_name)
    
    @mcp.tool()
    def physics_interactive_setup_flow(
        physics_name: str = "Laminar Flow",
        model_name: Optional[str] = None
    ) -> dict:
        """
        Interactive setup wizard for Laminar Flow boundary conditions.
        
        This tool helps identify and configure flow boundary conditions:
        1. Lists all available boundaries
        2. Prompts user to select inlet, outlet, and wall boundaries
        3. Configures appropriate boundary conditions
        
        Args:
            physics_name: Name of the Laminar Flow physics interface
            model_name: Model name (default: current model)
        
        Returns:
            Boundary information and setup instructions
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            # Get geometry boundaries
            boundaries_info = geometry_get_boundaries(None, model_name)
            if not boundaries_info.get("success"):
                return boundaries_info
            
            return {
                "success": True,
                "message": "Interactive Flow Setup - Please specify boundaries",
                "available_boundaries": boundaries_info["total_boundaries"],
                "boundaries": boundaries_info["boundaries"],
                "setup_instructions": {
                    "step1": "Identify which boundary numbers are INLETS (flow enters)",
                    "step2": "Identify which boundary numbers are OUTLETS (flow exits)",
                    "step3": "Use physics_configure_boundary to set conditions",
                },
                "boundary_condition_types": {
                    "InletBoundary": "Set inlet velocity (U0 parameter)",
                    "OutletBoundary": "Set outlet pressure (p0 parameter, default 0)",
                    "Wall": "No-slip wall (default for unspecified boundaries)",
                    "Symmetry": "Symmetry plane",
                },
                "example_usage": {
                    "inlet": "physics_configure_boundary(physics_name='Laminar Flow', boundary_condition='InletBoundary', boundary_selection=[1, 2], properties={'U0': '1[mm/s]'})",
                    "outlet": "physics_configure_boundary(physics_name='Laminar Flow', boundary_condition='OutletBoundary', boundary_selection=[3])",
                },
                "next_step": "Please tell me which boundary numbers to use for inlet(s) and outlet(s)",
            }
        except Exception as e:
            return {"success": False, "error": f"Interactive setup failed: {str(e)}"}
    
    @mcp.tool()
    def physics_setup_flow_boundaries(
        physics_name: str,
        inlet_boundaries: Sequence[int],
        outlet_boundaries: Sequence[int],
        inlet_velocity: str = "1[mm/s]",
        outlet_pressure: str = "0",
        model_name: Optional[str] = None
    ) -> dict:
        """
        Setup Laminar Flow boundary conditions with specified boundaries.
        
        This tool configures inlet velocity and outlet pressure boundary conditions
        for a fluid flow simulation.
        
        Args:
            physics_name: Name of the Laminar Flow physics interface
            inlet_boundaries: List of boundary numbers for inlets
            outlet_boundaries: List of boundary numbers for outlets
            inlet_velocity: Inlet velocity expression (default: "1[mm/s]")
            outlet_pressure: Outlet pressure expression (default: "0")
            model_name: Model name (default: current model)
        
        Returns:
            Configuration confirmation
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            jm = model.java
            
            # Find physics in component
            comp, physics, error = find_physics(model, physics_name)
            if error:
                return error
            bdim = int(comp.geom(_tags(comp.geom())[0]).getSDim()) - 1
            
            results = {"inlets": [], "outlets": []}
            
            # Add inlet boundary conditions
            for i, boundary in enumerate(inlet_boundaries):
                inlet_tag = f'inl{i+1}'
                inlet = physics.create(inlet_tag, 'InletBoundary', bdim)
                inlet.selection().set([int(boundary)])
                inlet.set('U0', inlet_velocity)
                inlet.label(f'Inlet {i+1} (Boundary {boundary})')
                results["inlets"].append({
                    "tag": inlet_tag,
                    "boundary": boundary,
                    "velocity": inlet_velocity
                })
            
            # Add outlet boundary conditions
            for i, boundary in enumerate(outlet_boundaries):
                outlet_tag = f'out{i+1}'
                outlet = physics.create(outlet_tag, 'OutletBoundary', bdim)
                outlet.selection().set([int(boundary)])
                outlet.set('p0', outlet_pressure)
                outlet.label(f'Outlet {i+1} (Boundary {boundary})')
                results["outlets"].append({
                    "tag": outlet_tag,
                    "boundary": boundary,
                    "pressure": outlet_pressure
                })
            
            return {
                "success": True,
                "physics": physics_name,
                "configured_boundaries": results,
                "inlet_velocity": inlet_velocity,
                "outlet_pressure": outlet_pressure,
                "message": f"Configured {len(inlet_boundaries)} inlet(s) and {len(outlet_boundaries)} outlet(s)",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to setup boundaries: {str(e)}"}

    @mcp.tool()
    def physics_interactive_setup_heat(
        physics_name: str = "Heat Transfer in Solids",
        model_name: Optional[str] = None
    ) -> dict:
        """
        Interactive setup wizard for Heat Transfer boundary conditions.
        
        This tool helps identify and configure thermal boundary conditions:
        1. Lists all available boundaries
        2. Shows typical boundary condition types for thermal analysis
        3. Provides setup instructions
        
        Args:
            physics_name: Name of the Heat Transfer physics interface
            model_name: Model name (default: current model)
        
        Returns:
            Boundary information and setup instructions
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            boundaries_info = geometry_get_boundaries(None, model_name)
            if not boundaries_info.get("success"):
                return boundaries_info
            
            return {
                "success": True,
                "message": "Interactive Heat Transfer Setup",
                "available_boundaries": boundaries_info["total_boundaries"],
                "boundaries": boundaries_info["boundaries"],
                "boundary_condition_types": {
                    "TemperatureBoundary": "Fixed temperature (heat sink/source)",
                    "HeatFluxBoundary": "Prescribed heat flux (heat source)",
                    "ConvectiveHeatFlux": "Convection cooling/heating",
                    "Symmetry": "Symmetry plane (adiabatic)",
                    "ThermalInsulation": "Thermal insulation (default)"
                },
                "typical_setup": {
                    "heat_source": "Use HeatFluxBoundary with q0 parameter (W/m^2)",
                    "heat_sink": "Use TemperatureBoundary with T0 parameter (K or degC)",
                    "convection": "Use ConvectiveHeatFlux with h and Text parameters"
                },
                "example_usage": {
                    "heat_source": "physics_setup_heat_boundaries(physics_name='Heat Transfer in Solids', heat_flux_boundaries=[1, 2], heat_flux_value='1e6[W/m^2]')",
                    "heat_sink": "physics_setup_heat_boundaries(physics_name='Heat Transfer in Solids', temperature_boundaries=[3], temperature_value='293.15[K]')"
                },
                "next_step": "Tell me which boundary numbers to use for heat source and heat sink",
            }
        except Exception as e:
            return {"success": False, "error": f"Interactive setup failed: {str(e)}"}

    @mcp.tool()
    def physics_setup_heat_boundaries(
        physics_name: str,
        heat_flux_boundaries: Sequence[int] = [],
        temperature_boundaries: Sequence[int] = [],
        convection_boundaries: Sequence[int] = [],
        heat_flux_value: str = "1e6[W/m^2]",
        temperature_value: str = "293.15[K]",
        convection_coeff: str = "10[W/(m^2*K)]",
        ambient_temp: str = "293.15[K]",
        model_name: Optional[str] = None
    ) -> dict:
        """
        Setup Heat Transfer boundary conditions with specified boundaries.
        
        This tool configures thermal boundary conditions for heat transfer simulation:
        - Heat flux boundaries (heat sources)
        - Temperature boundaries (heat sinks)
        - Convective cooling/heating boundaries
        
        Args:
            physics_name: Name of the Heat Transfer physics interface
            heat_flux_boundaries: List of boundary numbers for heat flux
            temperature_boundaries: List of boundary numbers for fixed temperature
            convection_boundaries: List of boundary numbers for convection
            heat_flux_value: Heat flux value (default: "1e6[W/m^2]")
            temperature_value: Temperature value (default: "293.15[K]" = 20°C)
            convection_coeff: Convection coefficient (default: "10[W/(m^2*K)]")
            ambient_temp: Ambient temperature for convection (default: "293.15[K]")
            model_name: Model name (default: current model)
        
        Returns:
            Configuration confirmation
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            jm = model.java
            
            comp, physics, error = find_physics(model, physics_name)
            if error:
                return error
            bdim = int(comp.geom(_tags(comp.geom())[0]).getSDim()) - 1
            
            results = {"heat_flux": [], "temperature": [], "convection": []}
            
            # Add heat flux boundaries (heat sources)
            for i, boundary in enumerate(heat_flux_boundaries):
                tag = f'hf{i+1}'
                bc = physics.create(tag, 'HeatFluxBoundary', bdim)
                bc.selection().set([int(boundary)])
                bc.set('q0', heat_flux_value)
                bc.label(f'Heat Flux {i+1} (Boundary {boundary})')
                results["heat_flux"].append({
                    "tag": tag,
                    "boundary": boundary,
                    "heat_flux": heat_flux_value
                })
            
            # Add temperature boundaries (heat sinks)
            for i, boundary in enumerate(temperature_boundaries):
                tag = f'temp{i+1}'
                bc = physics.create(tag, 'TemperatureBoundary', bdim)
                bc.selection().set([int(boundary)])
                bc.set('T0', temperature_value)
                bc.label(f'Temperature {i+1} (Boundary {boundary})')
                results["temperature"].append({
                    "tag": tag,
                    "boundary": boundary,
                    "temperature": temperature_value
                })
            
            # Add convection boundaries
            for i, boundary in enumerate(convection_boundaries):
                tag = f'conv{i+1}'
                bc = physics.create(tag, 'ConvectiveHeatFlux', bdim)
                bc.selection().set([int(boundary)])
                bc.set('h', convection_coeff)
                bc.set('Text', ambient_temp)
                bc.label(f'Convection {i+1} (Boundary {boundary})')
                results["convection"].append({
                    "tag": tag,
                    "boundary": boundary,
                    "h": convection_coeff,
                    "T_amb": ambient_temp
                })
            
            return {
                "success": True,
                "physics": physics_name,
                "configured_boundaries": results,
                "summary": {
                    "heat_flux_boundaries": len(heat_flux_boundaries),
                    "temperature_boundaries": len(temperature_boundaries),
                    "convection_boundaries": len(convection_boundaries)
                },
                "message": f"Configured {len(heat_flux_boundaries)} heat flux, {len(temperature_boundaries)} temperature, and {len(convection_boundaries)} convection boundaries",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to setup heat boundaries: {str(e)}"}

    @mcp.tool()
    def physics_boundary_selection(
        physics_name: str,
        boundary_condition_type: str,
        boundary_numbers: Sequence[int],
        properties: Optional[dict] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add any boundary condition by COMSOL feature type. Same as physics_configure_boundary.

        Common types: Heat Transfer (ht): TemperatureBoundary (T0), HeatFluxBoundary (q0).
        Laminar Flow (spf): InletBoundary (U0in), OutletBoundary (p0), WallBC.
        Solid Mechanics (solid): Fixed, BoundaryLoad (FperArea).

        Args:
            physics_name: Physics tag (e.g. "ht") or label
            boundary_condition_type: COMSOL feature type
            boundary_numbers: Boundary numbers
            properties: Property names and values
            model_name: Model name (default: current model)

        Returns:
            The feature tag, the boundaries COMSOL applied, and any property COMSOL rejected
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {"success": False, "error": f"Model not found: {model_name or 'no current model'}"}
        label = f"{boundary_condition_type} (Boundaries {list(boundary_numbers)})"
        return add_boundary_feature(model, physics_name, boundary_condition_type, boundary_numbers, properties, label=label)


