import pytest
"""Tests for physics creation, boundary features, boundary info, materials, and the solve check.

These tests reproduce the defects found in the first live run on COMSOL 6.4
(2026-10-19): physics created without a geometry tag (0D), lookup by label
instead of tag, and a solve that reported success with no solution.
"""

from src.tools.physics import (add_boundary_feature, add_coupling, add_physics, boundary_info, create_material,
                               domain_info, edge_info, find_physics, set_interface_property, set_material_properties)
from src.tools.geometry import add_feature
from src.tools.mesh import add_mesh_feature, mesh_counts, mesh_quality, set_element_sizes
from src.tools.results import add_dataset, add_plot, clear_plots, evaluate_stat, export_plot_image
from src.tools.study import check_solved, configure_solver, configure_step, create_study
from tests.mocks import FakeComponent, FakeMeshSeq, FakeModel, FakeTagList


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


def test_boundary_info_steps_inward_when_comsol_rejects_the_range_end():
    model, comp = block_model()
    comp._geom.reject_range_end = True
    faces = boundary_info(model)["boundaries"]
    assert all("error" not in face for face in faces)
    assert faces[0]["bounding_box"]["max"][1] == pytest.approx(0.02)


def test_edge_info_gives_ends_in_edge_direction():
    model, _ = block_model()
    result = edge_info(model)
    assert result["total_edges"] == 2
    first, second = result["edges"]
    assert first["start"] == [0.0, 0.0, 0.0]
    assert first["end"] == pytest.approx([0.1, 0.0, 0.0])
    assert second["center"] == pytest.approx([0.0, 0.01, 0.0])


def test_edge_info_rejects_2d():
    model, _ = block_model(sdim=2, n_boundaries=4)
    result = edge_info(model)
    assert not result["success"]


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


def test_set_material_properties_by_label_changes_the_value():
    model, comp = block_model()
    create_material(model, "TIM", {"thermal_conductivity": "3[W/(m*K)]"})
    result = set_material_properties(model, "TIM", {"thermal_conductivity": "15.4[W/(m*K)]"})
    assert result["success"] is True, result
    assert result["material"]["tag"] == "mat1"
    assert comp.material("mat1").group.values["thermalconductivity"] == ["15.4[W/(m*K)]"]


def test_set_material_properties_reports_a_missing_material_and_a_rejected_property():
    model, comp = block_model()
    create_material(model, "TIM", {"density": "1[kg/m^3]"})
    missing = set_material_properties(model, "Glue", {"density": "2[kg/m^3]"})
    assert missing["success"] is False and missing["available"] == ["TIM (mat1, comp1)"]
    rejected = set_material_properties(model, "mat1", {"density": "2[kg/m^3]", "conductivity_typo": "1"})
    assert rejected["success"] is False
    assert rejected["rejected_properties"]["conductivity_typo"] == "Unknown property conductivity_typo."
    assert comp.material("mat1").group.values["density"] == ["2[kg/m^3]"]


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


# Domain list

def test_boundary_info_gives_adjacent_domains():
    model, _ = block_model()
    faces = boundary_info(model)["boundaries"]
    assert all(f["domains"] == [1] for f in faces)


def test_domain_info_gives_box_and_exterior_faces():
    model, _ = block_model()
    result = domain_info(model)
    assert result["success"] is True, result
    assert result["total_domains"] == 1
    dom = result["domains"][0]
    assert dom["bounding_box"]["min"] == [0, 0, 0]
    assert dom["bounding_box"]["max"] == [0.1, 0.02, 0.01]
    assert dom["exterior_boundaries"] == [1, 2, 3, 4, 5, 6]


def test_domain_info_rejects_2d():
    model, _ = block_model(sdim=2)
    assert domain_info(model)["success"] is False


# Statistics over a selection

def test_evaluate_stat_builds_node_and_removes_it():
    model, _ = block_model()
    result = evaluate_stat(model, "max", "T", "domain", [1, 2], unit="degC")
    assert result["success"] is True, result
    assert result["value"] == 42.0
    assert result["entities"] == [1, 2]
    node = model.java.result().created[0]
    assert node.type == "MaxVolume"
    assert node.values["expr"] == ["T"]
    assert node.values["unit"] == ["degC"]
    assert model.java.result().numerical().tags() == []


def test_evaluate_stat_maps_boundary_integral():
    model, _ = block_model()
    evaluate_stat(model, "integral", "ht.ntflux", "boundary", [3])
    assert model.java.result().created[0].type == "IntSurface"


def test_evaluate_stat_uses_line_and_surface_nodes_in_2d():
    model, _ = block_model(sdim=2)
    evaluate_stat(model, "integral", "ht.ntflux", "boundary", [3])
    evaluate_stat(model, "average", "T", "domain", [1])
    assert [n.type for n in model.java.result().created] == ["IntLine", "AvSurface"]


# Plots

def test_add_dataset_sets_properties_and_numbers_tags():
    model, _ = block_model()
    first = add_dataset(model, "CutPlane", {"data": "dset2", "quickplane": "xy"}, label="Mid height")
    second = add_dataset(model, "CutPlane")
    assert (first["dataset"], second["dataset"]) == ("rds1", "rds2")
    node = model.java.result().datasets["rds1"]
    assert node.values == {"data": "dset2", "quickplane": "xy", "label": "Mid height"}


def test_add_dataset_reports_a_bad_type():
    model, _ = block_model()
    assert add_dataset(model, "BadSet")["success"] is False


def test_add_plot_builds_features_and_subfeatures():
    model, _ = block_model()
    result = add_plot(model, "PlotGroup3D", dataset="dset2", label="Air flow", features=[
        {"type": "Surface", "properties": {"expr": "T", "unit": "degC"},
         "subfeatures": [{"type": "Selection", "geometry": "geom1", "dimension": 2, "entities": [3, 4]}]},
        {"type": "Streamline", "entities": [1], "properties": {"zcoord": [0.1, 0.2], "selnumber": 40}},
    ])
    assert result["success"] is True, result
    group = model.java.result().groups[result["plot_group"]]
    assert group.type == "PlotGroup3D" and group.ran == 1
    assert group.values == {"data": "dset2", "label": "Air flow"}
    surface = group.children["surf1"]
    assert surface.values["unit"] == "degC"
    selection = surface.children["sel1"]
    assert selection.geom_dim == ("geom1", 2) and selection.selected == [3, 4]
    stream = group.children["stre2"]
    assert stream.selected == [1]
    assert stream.values["zcoord"] == ["0.1", "0.2"] and stream.values["selnumber"] == "40"


def test_add_plot_needs_a_feature_and_reports_the_error_detail():
    model, _ = block_model()
    assert add_plot(model, "PlotGroup3D", [])["success"] is False
    result = add_plot(model, "PlotGroup3D", [{"type": "BadPlot"}])
    assert result["success"] is False
    assert "second line" in result["error"]


def test_add_dataset_keeps_a_point_matrix_as_rows():
    model, _ = block_model()
    add_dataset(model, "CutLine3D", {"genpoints": [[0, 0.5, 1], [2, 0.5, 1]]})
    assert model.java.result().datasets["rds1"].values["genpoints"] == [["0", "0.5", "1"], ["2", "0.5", "1"]]


def test_clear_plots_removes_groups_and_added_datasets():
    model, _ = block_model()
    add_dataset(model, "CutPlane")
    add_plot(model, "PlotGroup2D", [{"type": "Surface"}])
    result = clear_plots(model)
    assert result["plot_groups"] == ["pg1"] and result["datasets"] == ["rds1"]
    assert model.java.result().tags() == [] and model.java.result().dataset().tags() == []


def test_export_plot_image_sets_png_size_and_removes_the_node(tmp_path):
    model, _ = block_model()
    target = tmp_path / "plots" / "t.png"
    result = export_plot_image(model, "pg1", str(target), width=800, height=600)
    assert result["success"] is True, result
    node = model.java.result().exported[0]
    assert node.parent == "pg1" and node.ran == 1
    assert node.values["pngfilename"] == str(target) and node.values["width"] == "800"
    assert node.values["background"] == "color" and node.values["logo2d"] == "off"
    assert target.parent.is_dir()
    assert model.java.result().export().tags() == []


def test_evaluate_stat_reports_dropped_entities():
    model, _ = block_model()
    result = evaluate_stat(model, "max", "T", "domain", [99])
    assert result["success"] is False
    assert "99" in result["error"]
    assert model.java.result().numerical().tags() == []


def test_evaluate_stat_rejects_bad_input():
    model, _ = block_model()
    assert evaluate_stat(model, "median", "T", "domain", [1])["success"] is False
    assert evaluate_stat(model, "max", "T", "edge", [1])["success"] is False
    assert evaluate_stat(model, "max", "T", "domain", [])["success"] is False


# Mesh counts

def test_mesh_counts_reads_java_counts():
    class Mesh:
        def getNumElem(self):
            return 1200

        def getNumVertex(self):
            return 300

    assert mesh_counts(Mesh()) == {"num_elements": 1200, "num_vertices": 300}


def test_mesh_counts_reports_failure():
    assert "counts_error" in mesh_counts(object())


# Flow and coupling tools

def test_add_physics_on_domain_numbers():
    comp = FakeComponent("comp1", {}, n_domains=3)
    model = FakeModel([comp])
    result = add_physics(model, "spf", domains=[2])
    assert result["success"] is True, result
    assert result["physics"]["domains"] == [2]
    assert comp.physics("spf").selection().domains == [2]


def test_add_physics_reports_dropped_domain():
    model = FakeModel([FakeComponent("comp1", {}, n_domains=2)])
    result = add_physics(model, "spf", domains=[9])
    assert result["success"] is False
    assert "9" in result["error"]


def test_domain_feature_uses_space_dimension():
    comp = FakeComponent("comp1", {}, physics=("ht",), n_domains=3)
    model = FakeModel([comp])
    result = add_boundary_feature(model, "ht", "FluidHeatTransferModel", [2], on_domains=True)
    assert result["success"] is True, result
    feat = comp.physics("ht").feature(result["feature"]["tag"])
    assert feat.dim == 3
    assert feat.selection().entities_set == [2]


def test_coupling_sets_named_physics():
    model, comp = block_model(physics=("ht",))
    add_physics(model, "spf")
    result = add_coupling(model, "NonIsothermalFlow", ["spf", "ht"])
    assert result["success"] is True, result
    node = comp.couplings[result["coupling"]["tag"]]
    assert node.dim == 3
    assert node.values == {"Fluid_physics": "spf", "Heat_physics": "ht"}


def test_coupling_reports_unknown_physics():
    model, _ = block_model(physics=("ht",))
    assert add_coupling(model, "NonIsothermalFlow", ["spf", "ht"])["success"] is False


def test_coupling_reports_rejected_property():
    model, _ = block_model(physics=("ht",))
    add_physics(model, "spf")
    result = add_coupling(model, "NonIsothermalFlow", ["spf", "ht"], {"typo": 1})
    assert result["success"] is False
    assert "typo" in result["rejected_properties"]


def test_create_study_numbers_tags_and_adds_step():
    model, _ = block_model()
    first = create_study(model)
    second = create_study(model)
    assert first["study"]["tag"] == "std1"
    assert second["study"]["tag"] == "std2"
    assert model.java.studies["std1"].steps == {"stat": "Stationary"}


def test_create_study_rejects_bad_input():
    model, _ = block_model()
    assert create_study(model, "Eigenvalue")["success"] is False
    create_study(model, study_tag="s")
    assert create_study(model, study_tag="s")["success"] is False


def test_element_sizes_set_custom_values():
    seq = FakeMeshSeq()
    set_element_sizes(seq, 0.001, None)
    assert seq.size.values == {"custom": "on", "hmax": "0.001"}
    assert seq.created == {"ftet1": "FreeTet"}


def test_element_sizes_default_changes_nothing():
    seq = FakeMeshSeq()
    set_element_sizes(seq, None, None)
    assert seq.size.values == {}
    assert seq.created == {}


def test_mesh_quality_reads_statistics():
    class Stat:
        measure = None

        def setQualityMeasure(self, name):
            Stat.measure = name

        def getMinQuality(self):
            return 0.01371

        def getMeanQuality(self):
            return 0.49

    class Mesh:
        def stat(self):
            return Stat()

    assert mesh_quality(Mesh()) == {"quality_measure": "skewness", "min_quality": 0.0137, "avg_quality": 0.49}
    assert Stat.measure == "skewness"


def test_mesh_quality_reports_failure():
    assert "quality_error" in mesh_quality(object())


# Manual mesh steps

def test_mesh_step_sets_entities_and_creates_sequence():
    model, comp = block_model()
    result = add_mesh_feature(model, "FreeTri", entity_dim=2, entities=[4, 5])
    assert result["success"] is True, result
    step = comp.mesh("mesh1").feature(result["feature"]["tag"])
    assert step.geom_args == ("geom1", 2)
    assert step.sel.entities_set == [4, 5]


def test_mesh_sub_step_goes_under_parent():
    model, comp = block_model()
    parent = add_mesh_feature(model, "FreeTri", entity_dim=2, entities=[4])["feature"]["tag"]
    result = add_mesh_feature(model, "Size", parent=parent, properties={"hmax": "1[mm]"})
    assert result["success"] is True, result
    size = comp.mesh("mesh1").feature(parent).feature(result["feature"]["tag"])
    assert size.values == {"hmax": "1[mm]"}


def test_mesh_sweep_sets_named_selections():
    model, comp = block_model()
    result = add_mesh_feature(model, "Sweep", entity_dim=3, entities=[1],
                              selections={"sourceface": [2], "targetface": [3]})
    assert result["success"] is True, result
    step = comp.mesh("mesh1").feature(result["feature"]["tag"])
    assert step.named["sourceface"].entities_set == [2]


def test_mesh_remaining_selection():
    model, comp = block_model()
    result = add_mesh_feature(model, "FreeTet", entities="remaining")
    assert comp.mesh("mesh1").feature(result["feature"]["tag"]).remaining_called is True


def test_mesh_step_rejected_property_removes_step():
    model, comp = block_model()
    result = add_mesh_feature(model, "FreeTet", properties={"typo": 1})
    assert result["success"] is False
    assert "typo" in result["rejected_properties"]
    assert comp.mesh("mesh1").feature().tags() == []


# Study step settings

def test_configure_step_sets_physics_and_sweep():
    model, _ = block_model()
    create_study(model)
    result = configure_step(model, "std1", solve_for={"/physics/ht": False},
                            properties={"useparam": "on", "pname": ["V0"]})
    assert result["success"] is True, result
    step = model.java.studies["std1"].step_nodes["stat"]
    assert step.solve_for == {"/physics/ht": False}
    assert step.values == {"useparam": "on", "pname": ["V0"]}


def test_configure_step_reports_rejected_property_and_unknown_study():
    model, _ = block_model()
    create_study(model)
    assert "typo" in configure_step(model, "std1", properties={"typo": 1})["rejected"]
    assert configure_step(model, "std9")["success"] is False


# Interface and solver properties

def test_set_interface_property_sets_and_reports_rejected():
    model, comp = block_model()
    add_physics(model, "LaminarFlow")
    result = set_interface_property(model, "spf", "AdvancedSettingProperty", {"PseudoTimeSetting": "On"})
    assert result["success"] is True, result
    assert comp.physics("spf").prop("AdvancedSettingProperty").values == {"PseudoTimeSetting": "On"}
    result = set_interface_property(model, "spf", "AdvancedSettingProperty", {"Nope": 1})
    assert result["success"] is False and "Nope" in result["rejected"]
    assert set_interface_property(model, "spf", "NoGroup", {"a": 1})["success"] is False


def test_configure_solver_creates_sequence_and_finds_nested_feature():
    model, _ = block_model()
    create_study(model, "Transient", "std1")
    result = configure_solver(model, "std1", {"Time": {"maxstepbdf": 1e-4}, "fc1": {"maxiter": 5}})
    assert result["success"] is True, result
    sol = model.java.sol(result["solution"])
    assert sol.study() == "std1" and sol.attached == "std1"
    assert result["features"]["t1"]["fc1"]["type"] == "FullyCoupled"
    assert sol.feature("t1").values == {"maxstepbdf": "0.0001"}
    assert sol.feature("t1").feature("fc1").values == {"maxiter": "5"}
    assert configure_solver(model, "std1", {"Missing": {"a": 1}})["success"] is False
    assert configure_solver(model, "std9", {})["success"] is False


def test_boundary_info_samples_edges_in_2d():
    class Edge2D:
        def run(self):
            pass

        def getSDim(self):
            return 2

        def getNBoundaries(self):
            return 1

        def getNDomains(self):
            return 1

        def getUpDown(self):
            return [[1], [0]]

        def edgeParamRange(self, edge):
            return [0.0, 1.0]

        def edgeX(self, edge, params):
            return [[2.0 * p, 3.0] for p in params]

        def edgeNormal(self, edge, params):
            return [[0.0, -1.0] for _ in params]

    model, comp = block_model()
    comp.geom = lambda tag=None: FakeTagList({"geom1": None}) if tag is None else Edge2D()
    result = boundary_info(model, "geom1")
    assert result["success"] is True, result
    assert result["boundaries"][0]["center"] == [1.0, 3.0]
    assert result["boundaries"][0]["normal"] == [0.0, -1.0]
    assert result["boundaries"][0]["bounding_box"] == {"min": [0.0, 3.0], "max": [2.0, 3.0]}


def test_add_feature_sets_a_hexahedron_matrix_as_strings():
    model = FakeModel([FakeComponent("comp1", {})])
    corners = [[0, 1, 0, 1, 0, 1, 0, 1], [0, 0, 0.5, 0.5, 0, 0, 0.25, 0.25], [0, 0, 0, 0, 1, 1, 1, 1]]
    result = add_feature(model, "Hexahedron", {"p": corners})
    assert result["success"] and result["feature"]["name"] == "hex1"
    feature = model.java.component("comp1").geom("geom1").feature("hex1")
    assert feature.values["p"][1] == ["0", "0", "0.5", "0.5", "0", "0", "0.25", "0.25"]


def test_add_feature_reports_a_rejected_property_and_picks_a_free_tag():
    model = FakeModel([FakeComponent("comp1", {})])
    add_feature(model, "Hexahedron", {"p": [[0] * 8] * 3})
    result = add_feature(model, "Hexahedron", {"p": [[0] * 8] * 3, "corner": 1})
    assert not result["success"] and result["feature"]["name"] == "hex2"
    assert list(result["rejected_properties"]) == ["corner"]

