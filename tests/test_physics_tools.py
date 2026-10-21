"""Tests for physics creation, boundary features, boundary info, materials, and the solve check.

These tests reproduce the defects found in the first live run on COMSOL 6.4
(2026-10-19): physics created without a geometry tag (0D), lookup by label
instead of tag, and a solve that reported success with no solution.
"""

from src.tools.physics import add_boundary_feature, add_physics, boundary_info, create_material, find_physics
from src.tools.study import check_solved
from tests.mocks import FakeComponent, FakeModel


def block_model(**kwargs):
    comp = FakeComponent("comp1", {}, n_domains=1, **kwargs)
    return FakeModel([comp]), comp


# Physics creation

def test_add_physics_passes_geometry_tag():
    model, comp = block_model()
    result = add_physics(model, "HeatTransfer")
    assert result["success"] is True, result
    assert result["physics"]["tag"] == "ht"
    assert result["physics"]["geometry"] == "geom1"
    assert comp.physics("ht").geom_tag == "geom1"


def test_add_physics_accepts_short_names():
    model, comp = block_model()
    assert add_physics(model, "solid")["physics"]["type"] == "SolidMechanics"
    assert add_physics(model, "es")["physics"]["tag"] == "es"


def test_second_interface_of_same_type_gets_new_tag():
    model, _ = block_model()
    add_physics(model, "ht")
    assert add_physics(model, "ht")["physics"]["tag"] == "ht2"


def test_add_physics_without_geometry_fails_with_hint():
    model, _ = block_model(geoms=())
    result = add_physics(model, "ht")
    assert result["success"] is False
    assert "geometry" in result["error"].lower()


def test_add_physics_applies_named_selection():
    model, comp = block_model()
    result = add_physics(model, "ht", selection_name="solid_domains")
    assert result["success"] is True
    assert comp.physics("ht").selection().named_selection == "solid_domains"


# Physics lookup

def test_find_physics_by_tag_and_label():
    model, _ = block_model()
    add_physics(model, "ht")
    assert find_physics(model, "ht")[2] is None
    assert find_physics(model, "Heat Transfer in Solids")[2] is None
    _, _, error = find_physics(model, "spf")
    assert error["success"] is False
    assert "Heat Transfer in Solids (ht)" in error["available"]


# Boundary features

def test_boundary_feature_uses_boundary_dimension_and_reads_back():
    model, comp = block_model()
    add_physics(model, "ht")
    result = add_boundary_feature(model, "ht", "TemperatureBoundary", [1], {"T0": "400[K]"})
    assert result["success"] is True, result
    feat = comp.physics("ht").feature(result["feature"]["tag"])
    assert feat.dim == 2
    assert result["applied_boundaries"] == [1]
    assert feat.values == {"T0": "400[K]"}


def test_two_features_get_different_tags():
    model, _ = block_model()
    add_physics(model, "ht")
    a = add_boundary_feature(model, "ht", "TemperatureBoundary", [1], {"T0": "400[K]"})
    b = add_boundary_feature(model, "ht", "TemperatureBoundary", [6], {"T0": "300[K]"})
    assert a["feature"]["tag"] != b["feature"]["tag"]


def test_rejected_property_is_a_failure():
    model, _ = block_model()
    add_physics(model, "ht")
    result = add_boundary_feature(model, "ht", "TemperatureBoundary", [1], {"Temp": "400[K]"})
    assert result["success"] is False
    assert result["rejected_properties"]["Temp"] == "Unknown parameter Temp."
    assert result["feature"]["removed"] is True
    assert result["feature"]["tag"] not in find_physics(model, "ht")[1].feature().tags()


def test_dropped_boundary_is_a_failure():
    model, _ = block_model()
    add_physics(model, "ht")
    result = add_boundary_feature(model, "ht", "TemperatureBoundary", [1, 9], {"T0": "400[K]"})
    assert result["success"] is False
    assert result["applied_boundaries"] == [1]
    assert "[9]" in result["error"]


def test_boundary_feature_on_unknown_physics():
    model, _ = block_model()
    result = add_boundary_feature(model, "ht", "TemperatureBoundary", [1])
    assert result["success"] is False
    assert "not found" in result["error"]


# Boundary info

def test_boundary_info_finds_geometry_by_tag():
    model, _ = block_model()
    result = boundary_info(model)
    assert result["success"] is True, result
    assert result["geometry"] == "geom1"
    assert result["total_boundaries"] == 6


def test_boundary_info_gives_center_and_normal():
    model, _ = block_model()
    faces = {b["boundary_number"]: b for b in boundary_info(model)["boundaries"]}
    assert faces[1]["normal"] == [-1.0, 0.0, 0.0]
    assert faces[1]["center"] == [0.0, 0.01, 0.005]
    assert faces[6]["normal"] == [1.0, 0.0, 0.0]
    assert faces[6]["bounding_box"] == {"min": [0.1, 0.0, 0.0], "max": [0.1, 0.02, 0.01]}


def test_boundary_info_unknown_geometry():
    model, _ = block_model()
    result = boundary_info(model, "geom7")
    assert result["success"] is False


# Material creation

def test_create_material_sets_properties_and_assigns():
    model, comp = block_model()
    result = create_material(model, "Aluminum", {"thermal_conductivity": "200[W/(m*K)]", "density": "2700[kg/m^3]"})
    assert result["success"] is True, result
    assert result["material"]["tag"] == "mat1"
    assert result["applied_domains"] == [1]
    assert comp.material("mat1").group.values["thermalconductivity"] == ["200[W/(m*K)]"]
    assert comp.material("mat1").label() == "Aluminum"


def test_create_material_rejected_property_is_a_failure():
    model, comp = block_model()
    result = create_material(model, "X", {"conductivity_typo": "1"})
    assert result["success"] is False
    assert result["rejected_properties"]["conductivity_typo"] == "Unknown property conductivity_typo."
    assert result["material"]["removed"] is True
    assert "mat1" not in comp.material().tags()


def test_create_material_needs_properties():
    model, _ = block_model()
    assert create_material(model, "X", {})["success"] is False


def test_second_material_gets_new_tag():
    model, _ = block_model()
    create_material(model, "A", {"density": "1[kg/m^3]"})
    assert create_material(model, "B", {"density": "2[kg/m^3]"})["material"]["tag"] == "mat2"


# Solve check

def test_solve_with_no_computed_solution_is_a_failure():
    comp = FakeComponent("comp1", {})
    result = check_solved(FakeModel([comp], solutions={"sol_fc": True}), None)
    assert result["success"] is False
    assert result["empty_solutions"] == ["sol_fc"]


def test_solve_with_computed_solution_is_a_success():
    comp = FakeComponent("comp1", {})
    result = check_solved(FakeModel([comp], solutions={"sol_fc": False}), None)
    assert result["success"] is True
    assert result["computed_solutions"] == ["sol_fc"]


def test_solve_with_no_solutions_at_all_is_a_failure():
    comp = FakeComponent("comp1", {})
    assert check_solved(FakeModel([comp]), None)["success"] is False
