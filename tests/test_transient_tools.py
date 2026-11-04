"""Tests for the transient study tools: start from a solved study, read probes, and reuse the last state.

The recipe comes from the P1 heat sink runs (2026-11-03): a Transient flow study (std3) that starts
from the last state of a steady flow study (std1), and a heat study (std2) on the last state of std3.
"""

import numpy as np

from src.tools.results import probe_time_series, solution_dataset
from src.tools.study import configure_solver, configure_transient, create_study, link_solution
from tests.mocks import FakeComponent, FakeModel

FLOW_ONLY = {"/physics/ht": False, "/multiphysics/noni1": False}


def solved_flow_model(sdim=3):
    """A model with a steady flow study std1 whose solution sol1 holds data."""
    model = FakeModel([FakeComponent("comp1", {}, n_domains=1, sdim=sdim)], solutions={"sol1": False})
    create_study(model, study_tag="std1")
    model.java.sol("sol1").study("std1")
    return model


# Transient study from a solved study

def test_transient_creates_study_and_starts_from_the_steady_solution():
    model = solved_flow_model()
    result = configure_transient(model, "std3", "std1", "range(0,1e-3,0.06)", rtol="1e-3", solve_for=FLOW_ONLY)
    assert result["success"] is True, result
    assert result["created"] is True and result["from_solution"] == "sol1" and result["label"] == "Study 2"
    assert model.java.studies["std3"].steps == {"time": "Transient"}
    step = model.java.studies["std3"].step_nodes["time"]
    assert step.solve_for == FLOW_ONLY
    assert step.values == {"tlist": "range(0,1e-3,0.06)", "usertol": "on", "rtol": "1e-3",
                           "useinitsol": "on", "initmethod": "sol", "initstudy": "std1", "solnum": "last",
                           "usesol": "on", "notsolmethod": "sol", "notstudy": "std1", "notsolnum": "last"}
    sol = model.java.sol(result["solution"])
    assert sol.feature("v1").values == {"initmethod": "sol", "initsol": "sol1", "solnum": "last",
                                        "notsolmethod": "sol", "notsol": "sol1", "notsolnum": "last"}


def test_transient_sets_the_solver_options_that_worked_on_the_flow_cell():
    model = solved_flow_model()
    result = configure_transient(model, "std3", "std1", "range(0,1e-3,0.005)")
    t1 = model.java.sol(result["solution"]).feature("t1")
    assert t1.values == {"consistent": "off"}
    # The direct solver is t1/d1, not the dDef node that comes first in the tree.
    assert t1.feature("fc1").values == {"linsolver": "d1", "jtech": "onevery"}
    assert result["solver_settings"] == {"Time": {"consistent": "off"},
                                         "FullyCoupled": {"linsolver": "d1", "jtech": "onevery"}}
    assert "rtol" not in model.java.studies["std3"].step_nodes["time"].values


def test_transient_options_can_pick_iterative_or_keep_defaults():
    model = solved_flow_model()
    result = configure_transient(model, "std3", "std1", "range(0,1e-3,0.005)", linear_solver="iterative")
    assert model.java.sol(result["solution"]).feature("t1").feature("fc1").values["linsolver"] == "i1"
    model = solved_flow_model()
    result = configure_transient(model, "std3", "std1", "range(0,1e-3,0.005)",
                                 consistent=None, linear_solver=None, jacobian_update=None)
    assert result["success"] is True and result["solver_settings"] == {}
    t1 = model.java.sol(result["solution"]).feature("t1")
    assert t1.values == {} and t1.feature("fc1").values == {}


def test_transient_needs_a_computed_source_solution():
    model = solved_flow_model()
    model.java.sol("sol1")._empty = True
    result = configure_transient(model, "std3", "std1", "range(0,1e-3,0.005)")
    assert result["success"] is False and "Solve it first" in result["error"]
    assert configure_transient(solved_flow_model(), "std3", "std9", "0 1")["success"] is False


def test_transient_reports_a_rejected_step_property():
    model = solved_flow_model()
    create_study(model, "Stationary", "std3")
    result = configure_transient(model, "std3", "std1", "0 1", step_tag="stat", solve_for=FLOW_ONLY)
    assert result["success"] is True, result
    model.java.studies["std3"].step_nodes["stat"].KNOWN = {"tlist"}
    result = configure_transient(model, "std3", "std1", "0 1", rtol="1e-3", step_tag="stat")
    assert result["success"] is False and "rtol" in result["rejected"]


def test_transient_warns_when_the_study_already_has_a_sequence():
    model = solved_flow_model()
    create_study(model, "Transient", "std3")
    configure_solver(model, "std3", {})
    result = configure_transient(model, "std3", "std1", "range(0,1e-3,0.005)")
    assert result["success"] is True and result["created"] is False
    assert "warning" in result


def test_linear_solver_tag_prefers_the_shallowest_node():
    from src.tools.study import _linear_solver_tag
    tree = {"t1": {"type": "Time", "dDef": {"type": "Direct"},
                   "i1": {"type": "Iterative", "mg1": {"type": "Multigrid", "cs": {"type": "CoarseSolver",
                                                                                     "d2": {"type": "Direct"}}}},
                   "d1": {"type": "Direct"}}}
    assert _linear_solver_tag(tree, "Direct") == "d1"
    assert _linear_solver_tag(tree, "Iterative") == "i1"
    assert _linear_solver_tag({"t1": {"type": "Time", "dDef": {"type": "Direct"}}}, "Direct") == "dDef"
    assert _linear_solver_tag(tree, "Missing") is None


# Another study on the last state

def test_use_solution_points_the_heat_study_at_the_last_transient_state():
    model = solved_flow_model()
    create_study(model, study_tag="std2")
    heat_sol = configure_solver(model, "std2", {})["solution"]
    transient = configure_transient(model, "std3", "std1", "range(0,1e-3,0.06)", solve_for=FLOW_ONLY)
    model.java.sol(transient["solution"])._empty = False
    result = link_solution(model, "std2", "std3")
    assert result["success"] is True, result
    assert result["step"] == "stat" and result["solution"] == heat_sol and result["label"] == "Study 2"
    assert model.java.studies["std2"].step_nodes["stat"].values == {
        "usesol": "on", "notsolmethod": "sol", "notstudy": "std3", "notsolnum": "last"}
    # The existing sequence keeps its own copy, so v1 must point at the transient solution too.
    assert model.java.sol(heat_sol).feature("v1").values == {
        "notsolmethod": "sol", "notsol": transient["solution"], "notsolnum": "last"}


def test_use_solution_rejects_bad_input():
    model = solved_flow_model()
    create_study(model, study_tag="std2")
    assert link_solution(model, "std2", "std1", initial=False, not_solved=False)["success"] is False
    assert link_solution(model, "std9", "std1")["success"] is False
    assert link_solution(model, "std2", "std2")["success"] is False  # std2 has no solution


# Probes against time

class ProbeModel(FakeModel):
    """A model whose evaluate returns a table, as MPh does for a global evaluation."""

    def __init__(self, table, sdim=3):
        super().__init__([FakeComponent("comp1", {}, n_domains=1, sdim=sdim)])
        self.table = np.asarray(table, dtype=float)
        self.calls = []

    def evaluate(self, expression, unit=None, dataset=None):
        self.calls.append({"expression": list(expression), "unit": unit, "dataset": dataset})
        if isinstance(self.table, Exception):
            raise self.table
        if getattr(self, "stationary", False) and "t" in expression:
            raise RuntimeError("Exception:\n\tFailed to evaluate expression.\nMessages:\n\tCannot evaluate "
                               "expression.\n\tUndefined variable.\n\t- Variable: t\n\t- Geometry: geom1")
        return self.table.squeeze()


def test_probe_builds_at3_columns_and_splits_the_table_per_point():
    # Columns: t, then u and p at point a, then u and p at point b.
    table = [[0.0, 1.0, 10.0, 2.0, 20.0], [0.001, 1.1, 11.0, 2.1, 21.0], [0.002, 1.2, 12.0, 2.2, 22.0]]
    model = ProbeModel(table)
    result = probe_time_series(model, [[0.01, 0.002, 0.0125], [0.02, 0, 0.001]], ["u", "p"],
                               dataset="Study 3//Solution 3", names=["a", "b"], unit="m/s")
    assert result["success"] is True, result
    assert model.calls[0]["expression"] == ["t", "at3(0.01,0.002,0.0125,u)", "at3(0.01,0.002,0.0125,p)",
                                            "at3(0.02,0,0.001,u)", "at3(0.02,0,0.001,p)"]
    assert model.calls[0]["unit"] == ["s", "m/s", "m/s", "m/s", "m/s"]
    assert result["times"] == [0.0, 0.001, 0.002] and result["n_times"] == 3
    assert result["probes"][0] == {"name": "a", "point": [0.01, 0.002, 0.0125],
                                   "values": {"u": [1.0, 1.1, 1.2], "p": [10.0, 11.0, 12.0]}}
    assert result["probes"][1]["values"]["p"] == [20.0, 21.0, 22.0]


def test_probe_keeps_one_row_when_mph_squeezes_a_single_time():
    model = ProbeModel([[0.005, 4.5]])
    result = probe_time_series(model, [[0.0, 0.0, 0.0]], "u")
    assert result["success"] is True, result
    assert result["times"] == [0.005] and result["probes"][0]["values"]["u"] == [4.5]
    assert result["probes"][0]["name"] == "p1" and model.calls[0]["unit"] is None


def test_probe_uses_at2_in_2d_and_checks_the_point_size():
    model = ProbeModel([[0.0, 1.0], [1.0, 2.0]], sdim=2)
    assert probe_time_series(model, [[0.1, 0.2]], "T")["success"] is True
    assert model.calls[0]["expression"] == ["t", "at2(0.1,0.2,T)"]
    result = probe_time_series(model, [[0.1, 0.2, 0.3]], "T")
    assert result["success"] is False and "2 coordinates" in result["error"]
    assert probe_time_series(model, [[0.1, 0.2]], "T", names=["a", "b"])["success"] is False
    assert probe_time_series(model, [], "T")["success"] is False


def test_probe_finds_the_dataset_of_a_study():
    model = ProbeModel([[0.0, 1.0]])
    create_study(model, "Transient", "std3")
    sol = configure_solver(model, "std3", {})["solution"]
    # COMSOL 6.4 labels: one "/". MPh names: "//" (2026-11-03 live check).
    model.java.result().add_solution_dataset("dset1", "Study 1/Solution 1", "sol0")
    model.java.result().add_solution_dataset("dset3", "Study 3/Solution 3", sol)
    assert solution_dataset(model, "std3") == "Study 3//Solution 3"
    result = probe_time_series(model, [[0, 0, 0]], "u", study_tag="std3")
    assert result["success"] is True and model.calls[0]["dataset"] == "Study 3//Solution 3"
    assert probe_time_series(model, [[0, 0, 0]], "u", study_tag="std9")["success"] is False


def test_probe_reports_an_evaluation_error():
    model = ProbeModel([[0.0, 1.0]])
    model.table = RuntimeError('Dataset "Cut Point 3D 1" does not refer to a solution.')
    result = probe_time_series(model, [[0, 0, 0]], "u")
    assert result["success"] is False and "does not refer to a solution" in result["error"]


def test_probe_on_a_stationary_solution_has_no_time_column():
    model = ProbeModel([[310.0, 0.62]])
    model.stationary = True
    result = probe_time_series(model, [[0.018, 0.002, 0.002]], ["T", "u"], unit="K")
    assert result["success"] is True, result
    assert result["times"] is None and result["n_times"] == 1
    assert result["probes"][0]["values"] == {"T": [310.0], "u": [0.62]}
    assert model.calls[1]["expression"] == ["at3(0.018,0.002,0.002,T)", "at3(0.018,0.002,0.002,u)"]
    assert model.calls[1]["unit"] == ["K", "K"]
