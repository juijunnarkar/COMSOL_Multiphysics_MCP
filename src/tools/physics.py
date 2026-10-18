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
        Add a physics interface to the model.
        
        Common physics types:
        - "Electrostatics" or "es": Electrostatic field analysis
        - "ElectricCurrents" or "ec": Electric current conduction
        - "SolidMechanics" or "solid": Structural stress analysis
        - "HeatTransfer" or "ht": Heat transfer in solids
        - "LaminarFlow" or "spf": Fluid dynamics
        
        Args:
            physics_type: Type identifier (e.g., "Electrostatics", "es")
            component_name: Component to add physics to (default: first component)
            model_name: Model name (default: current model)
        
        Returns:
            Created physics interface info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            components = model.components()
            if not components:
                component_name = None
            elif component_name is None:
                component_name = components[0]
            
            physics_node = model.create("physics", physics_type)
            
            return {
                "success": True,
                "physics": {
                    "name": physics_node.name() if hasattr(physics_node, 'name') else physics_type,
                    "type": physics_type,
                    "component": component_name,
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add physics: {str(e)}"}
    
    @mcp.tool()
    def physics_add_electrostatics(
        domain_selection: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add Electrostatics physics interface for electric field analysis.
        
        Args:
            domain_selection: Selection name for domains (default: all domains)
            model_name: Model name (default: current model)
        
        Returns:
            Created physics info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            physics_node = model.create("physics", "Electrostatics")
            
            if domain_selection:
                try:
                    physics_node.property("selection", domain_selection)
                except Exception:
                    pass
            
            return {
                "success": True,
                "physics": {
                    "name": physics_node.name() if hasattr(physics_node, 'name') else "Electrostatics",
                    "type": "Electrostatics",
                    "tag": "es",
                    "domain_selection": domain_selection,
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add Electrostatics: {str(e)}"}
    
    @mcp.tool()
    def physics_add_solid_mechanics(
        domain_selection: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add Solid Mechanics physics for structural analysis.
        
        Args:
            domain_selection: Selection name for domains (default: all domains)
            model_name: Model name (default: current model)
        
        Returns:
            Created physics info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            physics_node = model.create("physics", "SolidMechanics")
            
            if domain_selection:
                try:
                    physics_node.property("selection", domain_selection)
                except Exception:
                    pass
            
            return {
                "success": True,
                "physics": {
                    "name": physics_node.name() if hasattr(physics_node, 'name') else "Solid Mechanics",
                    "type": "SolidMechanics",
                    "tag": "solid",
                    "domain_selection": domain_selection,
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add Solid Mechanics: {str(e)}"}
    
    @mcp.tool()
    def physics_add_heat_transfer(
        domain_selection: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add Heat Transfer physics for thermal analysis.
        
        Args:
            domain_selection: Selection name for domains (default: all domains)
            model_name: Model name (default: current model)
        
        Returns:
            Created physics info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            physics_node = model.create("physics", "HeatTransfer")
            
            if domain_selection:
                try:
                    physics_node.property("selection", domain_selection)
                except Exception:
                    pass
            
            return {
                "success": True,
                "physics": {
                    "name": physics_node.name() if hasattr(physics_node, 'name') else "Heat Transfer",
                    "type": "HeatTransfer",
                    "tag": "ht",
                    "domain_selection": domain_selection,
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add Heat Transfer: {str(e)}"}
    
    @mcp.tool()
    def physics_add_laminar_flow(
        domain_selection: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Add Laminar Flow physics for fluid dynamics.
        
        Args:
            domain_selection: Selection name for domains (default: all domains)
            model_name: Model name (default: current model)
        
        Returns:
            Created physics info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            jm = model.java
            # Find the first component with a geometry
            comp = None
            geom_tag = None
            for c in jm.component():
                geom_tags = list(c.geom().tags())
                if geom_tags:
                    comp = c
                    geom_tag = geom_tags[0]
                    break

            if comp is None or geom_tag is None:
                return {"success": False, "error": "No component with geometry found. Build geometry first."}

            physics_node = comp.physics().create("spf", "LaminarFlow", geom_tag)

            return {
                "success": True,
                "physics": {
                    "name": "Laminar Flow",
                    "type": "LaminarFlow",
                    "tag": "spf",
                    "component": comp.tag(),
                    "geometry": geom_tag,
                    "domain_selection": domain_selection,
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to add Laminar Flow: {str(e)}"}
    
    @mcp.tool()
    def physics_configure_boundary(
        physics_name: str,
        boundary_condition: str,
        boundary_selection: Sequence[int],
        properties: Optional[dict] = None,
        model_name: Optional[str] = None
    ) -> dict:
        """
        Configure a boundary condition for a physics interface.
        
        Common boundary conditions for Electrostatics:
        - "Ground": Zero potential boundary
        - "ElectricPotential": Specified voltage
        - "SurfaceChargeDensity": Surface charge
        - "ZeroCharge": Zero normal displacement field
        
        Common for Solid Mechanics:
        - "Fixed": Fixed constraint
        - "Roller": Roller constraint
        - "Symmetry": Symmetry plane
        - "BoundaryLoad": Applied force/pressure
        
        Common for Heat Transfer:
        - "Temperature": Fixed temperature
        - "HeatFlux": Heat flux boundary
        - "ConvectiveHeatFlux": Convection cooling
        - "Symmetry": Symmetry (adiabatic)
        
        Args:
            physics_name: Name of the physics interface
            boundary_condition: Type of boundary condition
            boundary_selection: Boundary/edge numbers to apply condition to
            properties: Dictionary of property names and values
            model_name: Model name (default: current model)
        
        Returns:
            Created boundary condition info
        """
        model = session_manager.get_model(model_name)
        if model is None:
            return {
                "success": False,
                "error": f"Model not found: {model_name or 'no current model'}"
            }
        
        try:
            physics_interfaces = model.physics()
            if physics_name not in physics_interfaces:
                return {"success": False, "error": f"Physics interface not found: {physics_name}"}
            
            physics_node = model / "physics" / physics_name
            bc_node = physics_node.create(boundary_condition)
            
            bc_node.property("selection", list(boundary_selection))
            
            if properties:
                for prop_name, prop_value in properties.items():
                    try:
                        bc_node.property(prop_name, prop_value)
                    except Exception:
                        pass
            
            return {
                "success": True,
                "boundary_condition": {
                    "name": bc_node.name() if hasattr(bc_node, 'name') else boundary_condition,
                    "type": boundary_condition,
                    "physics": physics_name,
                    "selection": list(boundary_selection),
                    "properties": properties,
                }
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to configure boundary: {str(e)}"}
    
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
            physics_interfaces = model.physics()
            if physics_name not in physics_interfaces:
                return {"success": False, "error": f"Physics interface not found: {physics_name}"}
            
            physics_node = model / "physics" / physics_name
            features = []
            
            for child in physics_node.children():
                feat_info = {"name": child.name()}
                try:
                    feat_info["type"] = child.type() if hasattr(child, 'type') else "unknown"
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
            physics_interfaces = model.physics()
            if physics_name not in physics_interfaces:
                return {"success": False, "error": f"Physics interface not found: {physics_name}"}
            
            physics_node = model / "physics" / physics_name
            model.remove(physics_node)
            
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
        Get all boundaries from a geometry with their properties.
        
        Use this to identify which boundary numbers correspond to which faces
        before setting boundary conditions.
        
        Args:
            geometry_name: Geometry sequence name (default: first geometry)
            model_name: Model name (default: current model)
        
        Returns:
            List of boundaries with their numbers and areas
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
                return {"success": False, "error": "No geometries found"}
            
            target_geom = geometry_name or geometries[0]
            jm = model.java
            
            # Get component
            comp = None
            for c in jm.component():
                if target_geom in [g.tag() for g in c.geom()]:
                    comp = c
                    break
            
            if comp is None:
                return {"success": False, "error": "Geometry not found in components"}
            
            geom = comp.geom(target_geom)
            geom.run()
            
            # Get geometry info
            info = geom.info()
            boundaries = []
            
            for i in range(1, info.nboundary + 1):
                try:
                    # Get boundary entities
                    bd_info = {
                        "boundary_number": i,
                    }
                    boundaries.append(bd_info)
                except Exception:
                    boundaries.append({"boundary_number": i, "error": "Could not get info"})
            
            return {
                "success": True,
                "geometry": target_geom,
                "total_boundaries": info.nboundary,
                "total_domains": info.ndomain,
                "boundaries": boundaries,
                "hint": "Use boundary_number to set boundary conditions with physics_configure_boundary",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to get boundaries: {str(e)}"}
    
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
            physics_interfaces = model.physics()
            if physics_name not in physics_interfaces:
                return {"success": False, "error": f"Physics '{physics_name}' not found. Available: {physics_interfaces}"}
            
            # Get component and physics
            comp = None
            for c in jm.component():
                for p in c.physics():
                    if physics_name in p.label() or p.tag() == 'spf':
                        comp = c
                        physics = p
                        break
                if comp:
                    break
            
            if comp is None:
                return {"success": False, "error": "Could not find physics interface"}
            
            results = {"inlets": [], "outlets": []}
            
            # Add inlet boundary conditions
            for i, boundary in enumerate(inlet_boundaries):
                inlet_tag = f'inl{i+1}'
                inlet = physics.create(inlet_tag, 'InletBoundary')
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
                outlet = physics.create(outlet_tag, 'OutletBoundary')
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
            
            physics_interfaces = model.physics()
            if physics_name not in physics_interfaces:
                return {"success": False, "error": f"Physics '{physics_name}' not found. Available: {physics_interfaces}"}
            
            comp = None
            for c in jm.component():
                for p in c.physics():
                    if physics_name in p.label() or p.tag() == 'ht':
                        comp = c
                        physics = p
                        break
                if comp:
                    break
            
            if comp is None:
                return {"success": False, "error": "Could not find physics interface"}
            
            results = {"heat_flux": [], "temperature": [], "convection": []}
            
            # Add heat flux boundaries (heat sources)
            for i, boundary in enumerate(heat_flux_boundaries):
                tag = f'hf{i+1}'
                bc = physics.create(tag, 'HeatFluxBoundary')
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
                bc = physics.create(tag, 'TemperatureBoundary')
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
                bc = physics.create(tag, 'ConvectiveHeatFlux')
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
        properties: dict = {},
        model_name: Optional[str] = None
    ) -> dict:
        """
        Generic boundary condition setup with boundary selection.
        
        Use this tool to configure any boundary condition by specifying:
        1. The physics interface name
        2. The boundary condition type
        3. The boundary numbers to apply the condition to
        4. Properties specific to the boundary condition
        
        Common boundary condition types by physics:
        
        Heat Transfer (ht):
        - TemperatureBoundary: Set T0 (temperature)
        - HeatFluxBoundary: Set q0 (heat flux)
        - ConvectiveHeatFlux: Set h (coefficient), Text (ambient temp)
        
        Laminar Flow (spf):
        - InletBoundary: Set U0 (velocity)
        - OutletBoundary: Set p0 (pressure)
        - Wall: No-slip wall
        
        Solid Mechanics (solid):
        - Fixed: Fixed constraint
        - BoundaryLoad: Set Fx, Fy, Fz or FAx, FAy, FAz
        
        Args:
            physics_name: Name of the physics interface
            boundary_condition_type: Type of boundary condition
            boundary_numbers: List of boundary numbers
            properties: Dictionary of property names and values
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
            
            physics_interfaces = model.physics()
            if physics_name not in physics_interfaces:
                return {"success": False, "error": f"Physics '{physics_name}' not found. Available: {physics_interfaces}"}
            
            comp = None
            for c in jm.component():
                for p in c.physics():
                    if physics_name in p.label():
                        comp = c
                        physics = p
                        break
                if comp:
                    break
            
            if comp is None:
                return {"success": False, "error": "Could not find physics interface"}
            
            # Create boundary condition
            import random
            tag = f'bc_{random.randint(1000, 9999)}'
            bc = physics.create(tag, boundary_condition_type)
            bc.selection().set([int(b) for b in boundary_numbers])
            
            # Set properties
            for prop_name, prop_value in properties.items():
                try:
                    bc.set(prop_name, prop_value)
                except Exception as e:
                    pass  # Property might not exist
            
            bc.label(f'{boundary_condition_type} (Boundaries {list(boundary_numbers)})')
            
            return {
                "success": True,
                "physics": physics_name,
                "boundary_condition": {
                    "type": boundary_condition_type,
                    "tag": tag,
                    "boundaries": list(boundary_numbers),
                    "properties": properties
                },
                "message": f"Created {boundary_condition_type} on boundaries {list(boundary_numbers)}",
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to create boundary condition: {str(e)}"}


