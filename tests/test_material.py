"""Tests for physics_set_material (assign_material) without COMSOL."""

from src.tools.physics import assign_material
from tests.mocks import FakeComponent, FakeModel


def one_component(**kwargs):
    comp = FakeComponent("comp1", {"mat1": "Aluminum", "mat2": "Air"}, physics=["ht"], **kwargs)
    return FakeModel([comp]), comp


def test_assign_by_tag_sets_and_reads_back_domains():
    model, comp = one_component()
    result = assign_material(model, "mat1", [1, 2])
    assert result["success"] is True
    assert result["material"] == {"tag": "mat1", "label": "Aluminum", "component": "comp1"}
    assert result["applied_domains"] == [1, 2]
    assert comp.material("mat1").selection().domains == [1, 2]


def test_assign_by_label():
    model, comp = one_component()
    result = assign_material(model, "Air", [3])
    assert result["success"] is True
    assert result["material"]["tag"] == "mat2"
    assert comp.material("mat2").selection().domains == [3]


def test_no_domains_selects_all():
    model, comp = one_component(n_domains=4)
    result = assign_material(model, "mat1")
    assert result["success"] is True
    assert result["requested_domains"] == "all"
    assert result["applied_domains"] == [1, 2, 3, 4]
    assert comp.material("mat1").selection().calls == ["all"]


def test_read_back_uses_geometry_dimension_2d():
    model, _ = one_component(sdim=2)
    result = assign_material(model, "mat1", [1])
    assert result["success"] is True
    assert result["applied_domains"] == [1]


def test_domain_that_comsol_drops_is_a_failure():
    # The geometry has 3 domains, so COMSOL does not apply domain 7.
    model, _ = one_component(n_domains=3)
    result = assign_material(model, "mat1", [1, 7])
    assert result["success"] is False
    assert result["applied_domains"] == [1]
    assert "[7]" in result["error"]


def test_empty_selection_after_assignment_is_a_failure():
    model, _ = one_component(n_domains=3)
    result = assign_material(model, "mat1", [9])
    assert result["success"] is False
    assert result["applied_domains"] == []


def test_material_not_found_lists_available():
    model, _ = one_component()
    result = assign_material(model, "Copper", [1])
    assert result["success"] is False
    assert "Aluminum (mat1, comp1)" in result["available"]
    assert "Material Link" in result["hint"]


def test_invalid_domain_numbers():
    model, _ = one_component()
    assert assign_material(model, "mat1", [0])["success"] is False
    assert assign_material(model, "mat1", ["a"])["success"] is False
    assert assign_material(model, "mat1", [])["success"] is False


def test_same_label_in_two_components_needs_physics_name():
    comp1 = FakeComponent("comp1", {"mat1": "Aluminum"}, physics=["ht"])
    comp2 = FakeComponent("comp2", {"mat1": "Aluminum"}, physics=["solid"])
    model = FakeModel([comp1, comp2])

    ambiguous = assign_material(model, "Aluminum", [1])
    assert ambiguous["success"] is False
    assert len(ambiguous["matches"]) == 2

    picked = assign_material(model, "Aluminum", [1], physics_name="solid")
    assert picked["success"] is True
    assert picked["material"]["component"] == "comp2"
    assert comp1.material("mat1").selection().domains == []


def test_unknown_physics_name():
    model, _ = one_component()
    result = assign_material(model, "mat1", [1], physics_name="spf")
    assert result["success"] is False
    assert "spf" in result["error"]
