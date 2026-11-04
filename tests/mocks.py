"""Small stand-ins for the COMSOL Java API, for tests that run without COMSOL.

Each class copies only the calls that the code under test makes, with the
same names as the Java API (component(), material(tag), selection(), ...).
Where COMSOL rejects a call, the fake raises the same kind of error, so a
test fails when a tool passes the wrong arguments.
"""


class FakeSelection:
    """An entity selection. Entities above n_entities are dropped, as COMSOL does."""

    def __init__(self, n_entities: int, dim: int):
        self.n_entities = n_entities
        self.dim = dim
        self.entities_set: list[int] = []
        self.named_selection = None
        self.calls: list[str] = []

    # Material tests read .domains and .sdim. Keep these names.
    @property
    def domains(self):
        return self.entities_set

    @property
    def n_domains(self):
        return self.n_entities

    @property
    def sdim(self):
        return self.dim

    def set(self, entities):
        self.calls.append("set")
        self.entities_set = [int(e) for e in entities if 1 <= int(e) <= self.n_entities]

    def all(self):
        self.calls.append("all")
        self.entities_set = list(range(1, self.n_entities + 1))

    def named(self, name):
        self.calls.append("named")
        self.named_selection = name

    def entities(self, dim=None):
        if dim is not None and dim != self.dim:
            raise RuntimeError(f"No entities at dimension {dim}")
        return list(self.entities_set)


class FakeTagList:
    def __init__(self, tags=(), create=None, lookup=None):
        self._tags = list(tags)
        self._create = create
        self._lookup = lookup

    def get(self, tag):
        if self._lookup is None:
            raise RuntimeError("get is not supported here")
        return self._lookup(tag)

    def tags(self):
        return list(self._tags)

    def create(self, tag, *args):
        if self._create is None:
            raise RuntimeError("create is not supported here")
        node = self._create(tag, *args)
        self._tags.append(tag)
        return node

    def remove(self, tag):
        self._tags.remove(tag)


class FakePropertyGroup:
    KNOWN = {"thermalconductivity", "density", "heatcapacity", "youngsmodulus", "poissonsratio",
             "thermalexpansioncoefficient", "electricconductivity", "relpermittivity",
             "dynamicviscosity", "ratioofspecificheat"}

    def __init__(self):
        self.values: dict[str, list] = {}

    def set(self, name, value):
        if name not in self.KNOWN:
            raise RuntimeError(f"Exception:\n\tcom.comsol.util.exceptions.FlException: Unknown property {name}.\nMessages:\n\tUnknown property {name}.")
        self.values[name] = list(value)


class FakeMaterial:
    def __init__(self, label: str, sel: FakeSelection):
        self._label = label
        self._sel = sel
        self.group = FakePropertyGroup()

    def label(self, new=None):
        if new is not None:
            self._label = new
        return self._label

    def selection(self):
        return self._sel

    def propertyGroup(self, tag):
        assert tag == "def"
        return self.group


class FakeInterfaceGroup:
    def __init__(self, known):
        self.known = known
        self.values: dict[str, str] = {}

    def set(self, name, value):
        if name not in self.known:
            raise RuntimeError(f"Exception:\n\tcom.comsol.util.exceptions.FlException: Unknown property {name}.")
        self.values[name] = value


class FakeFeature:
    """A physics feature. set() accepts only the names in `params`, as COMSOL does."""

    PARAMS = {
        "TemperatureBoundary": {"T0"},
        "HeatFluxBoundary": {"q0_input", "HeatFluxType", "h", "Text"},
        "Fixed": set(),
        "FluidHeatTransferModel": set(),
        "InletBoundary": {"U0in"},
        "OutletBoundary": {"p0"},
        "Hexahedron": {"p"},
        "Block": {"pos", "size"},
    }

    def __init__(self, tag, ftype, dim, n_entities):
        self._tag = tag
        self.type = ftype
        self.dim = dim
        self._label = ftype
        self.values: dict[str, str] = {}
        self._sel = FakeSelection(n_entities, dim)

    def tag(self):
        return self._tag

    def label(self, new=None):
        if new is not None:
            self._label = new
        return self._label

    def getType(self):
        return self.type

    def selection(self):
        return self._sel

    def set(self, name, value):
        if name not in self.PARAMS.get(self.type, set()):
            raise RuntimeError(f"Exception:\n\tcom.comsol.util.exceptions.FlException: Unknown parameter {name}.\nMessages:\n\tUnknown parameter {name}.")
        self.values[name] = value


class FakePhysics:
    LABELS = {"HeatTransfer": "Heat Transfer in Solids", "SolidMechanics": "Solid Mechanics",
              "Electrostatics": "Electrostatics", "LaminarFlow": "Laminar Flow"}

    def __init__(self, tag, ptype, geom_tag, comp):
        self._tag = tag
        self.type = ptype
        self.geom_tag = geom_tag
        self._comp = comp
        self._label = self.LABELS.get(ptype, ptype)
        self.features: dict[str, FakeFeature] = {}
        self._feature_list = FakeTagList(create=self._create_feature)
        self._sel = FakeSelection(comp.n_domains, comp.sdim)
        self.groups = {"AdvancedSettingProperty": FakeInterfaceGroup({"PseudoTimeSetting", "useBNS"})}

    def tag(self):
        return self._tag

    def label(self, new=None):
        if new is not None:
            self._label = new
        return self._label

    def selection(self):
        return self._sel

    def prop(self, group):
        if group not in self.groups:
            raise RuntimeError("Exception:\n\tUnknown property.")
        return self.groups[group]

    def _create_feature(self, tag, ftype, dim=None):
        if dim is None:
            raise RuntimeError("Feature dimension missing")
        n = self._comp.n_boundaries if dim == self._comp.sdim - 1 else self._comp.n_domains
        feat = FakeFeature(tag, ftype, dim, n)
        self.features[tag] = feat
        return feat

    def create(self, tag, ftype, dim=None):
        return self._feature_list.create(tag, ftype, dim)

    def feature(self, tag=None):
        if tag is None:
            return self._feature_list
        return self.features[tag]


class FakeGeom:
    """A built box geometry from (0, 0, 0) to size, with COMSOL's face numbering for a Block."""

    # Face number -> (fixed axis, fixed at max?, the two free axes)
    FACES = {1: (0, False, (1, 2)), 2: (1, False, (0, 2)), 3: (2, False, (0, 1)),
             4: (2, True, (0, 1)), 5: (1, True, (0, 2)), 6: (0, True, (1, 2))}

    def __init__(self, sdim: int, size=(0.1, 0.02, 0.01), n_domains=1):
        self._sdim = sdim
        self.size = size
        self._n_domains = n_domains
        self.runs = 0
        self.reject_range_end = False
        self.features = {}
        self._feature_list = FakeTagList(create=self._create_feature)

    def _create_feature(self, tag, ftype):
        self.features[tag] = FakeFeature(tag, ftype, self._sdim, 0)
        return self.features[tag]

    def feature(self, tag=None):
        return self._feature_list if tag is None else self.features[tag]

    def getSDim(self):
        return self._sdim

    def run(self):
        self.runs += 1

    def getNBoundaries(self):
        return 6 if self._sdim == 3 else 4

    def getNDomains(self):
        return self._n_domains

    def getNEdges(self):
        return 2

    def edgeParamRange(self, edge):
        return [0.0, self.size[edge - 1] * 1000]

    def edgeX(self, edge, params):
        # Edge 1 lies along x and edge 2 along y, both from the origin.
        return [[s / 1000 if k == edge - 1 else 0.0 for k in range(3)] for s in params]

    def getUpDown(self):
        # One domain inside every face. Domain 0 is the outside.
        return [[0] * self.getNBoundaries(), [1] * self.getNBoundaries()]

    def faceParamRange(self, face):
        _, _, (a, b) = self.FACES[face]
        return [0.0, self.size[a] * 1000, 0.0, self.size[b] * 1000]

    def faceX(self, face, uv):
        axis, at_max, (a, b) = self.FACES[face]
        out = []
        for u, v in uv:
            if self.reject_range_end and (u >= self.size[a] * 1000 or v >= self.size[b] * 1000):
                raise ValueError("Face parameter out of range")
            p = [0.0, 0.0, 0.0]
            p[axis] = self.size[axis] if at_max else 0.0
            p[a], p[b] = u / 1000, v / 1000
            out.append(p)
        return out

    def faceNormal(self, face, uv):
        axis, at_max, _ = self.FACES[face]
        n = [0.0, 0.0, 0.0]
        n[axis] = 1.0 if at_max else -1.0
        return [list(n) for _ in uv]


class FakeComponent:
    def __init__(self, tag, materials: dict, physics=(), n_domains=3, sdim=3, n_boundaries=6, geoms=("geom1",)):
        self._tag = tag
        self.sdim = sdim
        self.n_domains = n_domains
        self.n_boundaries = n_boundaries
        self._geom_tags = list(geoms)
        self._geom = FakeGeom(sdim, n_domains=n_domains)
        self._materials = {
            mtag: FakeMaterial(label, FakeSelection(n_domains, sdim))
            for mtag, label in materials.items()
        }
        self._material_list = FakeTagList(self._materials, create=self._create_material)
        self._physics = {}
        self._physics_list = FakeTagList(create=self._create_physics)
        self.couplings = {}
        self.meshes = {}
        self._mesh_list = FakeTagList(create=self._create_mesh)
        self._coupling_list = FakeTagList(create=self._create_coupling)
        for ptag in physics:
            self._physics_list.create(ptag, "HeatTransfer", self._geom_tags[0] if self._geom_tags else "geom1")

    def tag(self):
        return self._tag

    def _create_material(self, tag, mtype):
        assert mtype == "Common"
        mat = FakeMaterial("Material", FakeSelection(self.n_domains, self.sdim))
        self._materials[tag] = mat
        return mat

    def material(self, tag=None):
        if tag is None:
            return self._material_list
        return self._materials[tag]

    def _create_physics(self, tag, ptype, geom_tag=None):
        if geom_tag is None:
            # This is what COMSOL does when the geometry tag is missing.
            raise RuntimeError("Exception:\n\tFailed to initialize physics interface.\n"
                               "\tThe physics interface does not support the spatial dimension.\n\t- Spatial dimension: 0D")
        if geom_tag not in self._geom_tags:
            raise RuntimeError(f"Unknown geometry {geom_tag}")
        phys = FakePhysics(tag, ptype, geom_tag, self)
        self._physics[tag] = phys
        return phys

    def physics(self, tag=None):
        if tag is None:
            return self._physics_list
        return self._physics[tag]

    def _create_coupling(self, tag, ctype, dim):
        if dim != self.sdim:
            raise RuntimeError("The coupling needs the space dimension")
        self.couplings[tag] = FakeCoupling(tag, ctype, dim)
        return self.couplings[tag]

    def multiphysics(self):
        return self._coupling_list

    def _create_mesh(self, tag):
        self.meshes[tag] = FakeMeshStep("Mesh")
        return self.meshes[tag]

    def mesh(self, tag=None):
        return self._mesh_list if tag is None else self.meshes[tag]

    def geom(self, tag=None):
        if tag is None:
            return FakeTagList(self._geom_tags, lookup=self.geom)
        if tag not in self._geom_tags:
            raise RuntimeError(f"Unknown geometry {tag}")
        return self._geom


class FakeCoupling:
    PARAMS = {"NonIsothermalFlow": {"Fluid_physics", "Heat_physics"}}

    def __init__(self, tag, ctype, dim):
        self.tag = tag
        self.type = ctype
        self.dim = dim
        self.values = {}

    def set(self, name, value):
        if name not in self.PARAMS.get(self.type, set()):
            raise RuntimeError(f"Exception:\n\tUnknown parameter {name}.\nMessages:\n\tUnknown parameter {name}.")
        self.values[name] = value


class FakeStudyStep:
    KNOWN = {"useparam", "pname", "plistarr", "punit", "tlist", "usertol", "rtol",
             "useinitsol", "initmethod", "initstudy", "solnum", "usesol", "notsolmethod", "notstudy", "notsolnum"}

    def __init__(self):
        self.solve_for = {}
        self.values = {}

    def setSolveFor(self, path, active):
        self.solve_for[path] = active

    def set(self, name, value):
        if name not in self.KNOWN:
            raise RuntimeError(f"Exception:\n\tUnknown property {name}.")
        self.values[name] = value


class FakeStudy:
    def __init__(self, label="Study"):
        self.steps = {}
        self.step_nodes = {}
        self._label = label

    def label(self):
        return self._label

    def create(self, tag, stype):
        self.steps[tag] = stype
        self.step_nodes[tag] = FakeStudyStep()

    def feature(self, tag=None):
        return FakeTagList(self.step_nodes) if tag is None else self.step_nodes[tag]


class FakeSizeNode:
    def __init__(self):
        self.values = {}

    def set(self, name, value):
        self.values[name] = value


class FakeMeshSeq:
    def __init__(self):
        self.size = FakeSizeNode()
        self.created = {}

    def create(self, tag, ftype):
        self.created[tag] = ftype

    def feature(self, tag):
        assert tag == "size"
        return self.size


class FakeMeshStep:
    PARAMS = {"Size": {"hmax", "hmin"}, "Distribution": {"numelem"}, "FreeTri": set(), "Sweep": set(), "FreeTet": set()}

    def __init__(self, ftype):
        self.type = ftype
        self.values = {}
        self.sel = FakeSelection(1000, 3)
        self.geom_args = None
        self.remaining_called = False
        self.named: dict[str, FakeSelection] = {}
        self._steps = {}
        self._list = FakeTagList(create=self._create)

    def _create(self, tag, ftype):
        self._steps[tag] = FakeMeshStep(ftype)
        return self._steps[tag]

    def create(self, tag, ftype):
        return self._list.create(tag, ftype)

    def feature(self, tag=None):
        return self._list if tag is None else self._steps[tag]

    def selection(self, name=None):
        if name is None:
            step = self

            class Sel(FakeSelection):
                def geom(self, tag, dim):
                    step.geom_args = (tag, dim)

                def remaining(self):
                    step.remaining_called = True
            sel = Sel(1000, 3)
            self.sel = sel
            return sel
        if name not in {"sourceface", "targetface"}:
            raise RuntimeError(f"Exception:\n\tUnknown selection {name}.")
        return self.named.setdefault(name, FakeSelection(1000, 2))

    def set(self, name, value):
        if name not in self.PARAMS.get(self.type, set()):
            raise RuntimeError(f"Exception:\n\tUnknown property {name}.")
        self.values[name] = value


class FakeSolverFeature:
    def __init__(self, stype, children=None, known=()):
        self.stype = stype
        self.children = children or {}
        self.known = set(known)
        self.values: dict[str, str] = {}

    def getType(self):
        return self.stype

    def feature(self, tag=None):
        return FakeTagList(self.children) if tag is None else self.children[tag]

    def set(self, name, value):
        if name not in self.known:
            raise RuntimeError("Exception:\n\tUnknown property.")
        self.values[name] = value


class FakeSolution:
    def __init__(self, empty: bool):
        self._empty = empty
        self._study = None
        self.children = {}

    def isEmpty(self):
        return self._empty

    def study(self, tag=None):
        if tag is not None:
            self._study = tag
        return self._study

    def attach(self, tag):
        self.attached = tag

    def createAutoSequence(self, study_tag):
        # The default sequence of a Transient laminar flow study, as COMSOL 6.4 builds it (2026-11-03).
        # A dDef node of type Direct comes first, and the multigrid coarse solver i1/mg1/cs/d1 comes
        # before the top-level d1.
        variables = {"initmethod", "initsol", "solnum", "notsolmethod", "notsol", "notsolnum"}
        coarse = FakeSolverFeature("CoarseSolver", {"dDef": FakeSolverFeature("Direct"), "d1": FakeSolverFeature("Direct")})
        self.children = {
            "st1": FakeSolverFeature("StudyStep"),
            "v1": FakeSolverFeature("Variables", known=variables),
            "t1": FakeSolverFeature("Time", {
                "dDef": FakeSolverFeature("Direct"),
                "aDef": FakeSolverFeature("Advanced"),
                "fc1": FakeSolverFeature("FullyCoupled", known={"maxiter", "linsolver", "jtech"}),
                "i1": FakeSolverFeature("Iterative", {"mg1": FakeSolverFeature("Multigrid", {"cs": coarse})}),
                "d1": FakeSolverFeature("Direct"),
            }, known={"maxstepbdf", "consistent"}),
        }

    def feature(self, tag=None):
        return FakeTagList(self.children) if tag is None else self.children[tag]


class FakeNumerical:
    """A numerical evaluation node. Entities above n_entities are dropped, as COMSOL does."""

    def __init__(self, tag, ntype, n_entities, value):
        self.tag = tag
        self.type = ntype
        self.values = {}
        self._value = value
        self._sel = FakeSelection(n_entities, 3)

    def selection(self):
        return self._sel

    def set(self, name, value):
        self.values[name] = value

    def getReal(self):
        return [[self._value]]


class FakePlotSelection:
    def __init__(self, node):
        self.node = node

    def geom(self, tag, dimension):
        self.node.geom_dim = (tag, dimension)

    def set(self, entities):
        self.node.selected = list(entities)


class FakePlotNode:
    """A dataset, plot group, plot feature, or export node."""

    def __init__(self, tag, ntype, parent=None):
        self.tag = tag
        self.type = ntype
        self.parent = parent
        self.values = {}
        self.children = {}
        self.ran = 0
        self.geom_dim = None
        self.selected = None

    def set(self, name, value):
        self.values[name] = value

    def label(self, text):
        self.values["label"] = text

    def create(self, tag, ntype):
        if ntype == "BadPlot":
            raise RuntimeError("Unknown feature type BadPlot\nsecond line")
        self.children[tag] = FakePlotNode(tag, ntype, self)
        return self.children[tag]

    def selection(self):
        return FakePlotSelection(self)

    def run(self):
        self.ran += 1


class FakeSolutionDataset:
    """A Solution dataset: it shows one solution and has a label."""

    def __init__(self, label, sol_tag):
        self._label = label
        self.sol_tag = sol_tag

    def getType(self):
        return "Solution"

    def getString(self, name):
        assert name == "solution"
        return self.sol_tag

    def label(self):
        return self._label


class FakeResult:
    def __init__(self):
        self.created: list[FakeNumerical] = []
        self.value = 42.0
        self.n_entities = 6
        self._list = FakeTagList(create=self._create)
        self.groups = {}
        self.datasets = {}
        self.exported = []
        self._groups = FakeTagList(create=self._create_group)
        self._datasets = FakeTagList(create=self._create_dataset)
        self._exports = FakeTagList(create=self._create_export)

    def _create_group(self, tag, ntype):
        self.groups[tag] = FakePlotNode(tag, ntype)
        return self.groups[tag]

    def _create_dataset(self, tag, ntype):
        if ntype == "BadSet":
            raise RuntimeError("Unknown dataset type")
        self.datasets[tag] = FakePlotNode(tag, ntype)
        return self.datasets[tag]

    def _create_export(self, tag, group, ntype):
        node = FakePlotNode(tag, ntype, group)
        self.exported.append(node)
        return node

    def create(self, tag, ntype):
        return self._groups.create(tag, ntype)

    def tags(self):
        return self._groups.tags()

    def remove(self, tag):
        self._groups.remove(tag)

    def dataset(self, tag=None):
        return self._datasets if tag is None else self.datasets[tag]

    def add_solution_dataset(self, tag, label, sol_tag):
        """Add a Solution dataset, as COMSOL does after a solve."""
        self.datasets[tag] = FakeSolutionDataset(label, sol_tag)
        self._datasets._tags.append(tag)

    def export(self):
        return self._exports

    def _create(self, tag, ntype):
        if ntype not in {f"{op}{dim}" for op in ("Max", "Min", "Av", "Int") for dim in ("Volume", "Surface", "Line")}:
            raise RuntimeError(f"Unknown numerical type {ntype}")
        node = FakeNumerical(tag, ntype, self.n_entities, self.value)
        self.created.append(node)
        return node

    def numerical(self):
        return self._list


class FakeJavaModel:
    def __init__(self, components, solutions=None):
        self._components = list(components)
        self._solutions = {t: FakeSolution(e) for t, e in (solutions or {}).items()}
        self._result = FakeResult()
        self.studies = {}
        self._study_list = FakeTagList(create=self._create_study)

    def result(self):
        return self._result

    def _create_study(self, tag):
        self.studies[tag] = FakeStudy(f"Study {len(self.studies) + 1}")
        return self.studies[tag]

    def study(self, tag=None):
        return self._study_list if tag is None else self.studies[tag]

    def component(self, tag=None):
        if tag is None:
            return list(self._components)
        return next(c for c in self._components if c.tag() == tag)

    def _create_sol(self, tag):
        self._solutions[tag] = FakeSolution(True)
        return self._solutions[tag]

    def sol(self, tag=None):
        if tag is None:
            return FakeTagList(self._solutions, create=self._create_sol)
        return self._solutions[tag]


class FakeModel:
    """Stands in for mph.Model: the code under test only reads .java."""

    def __init__(self, components, solutions=None):
        self.java = FakeJavaModel(components, solutions)
