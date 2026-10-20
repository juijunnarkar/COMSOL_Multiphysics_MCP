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

    def entities(self, dim):
        if dim != self.dim:
            raise RuntimeError(f"No entities at dimension {dim}")
        return list(self.entities_set)


class FakeTagList:
    def __init__(self, tags=(), create=None):
        self._tags = list(tags)
        self._create = create

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


class FakeFeature:
    """A physics feature. set() accepts only the names in `params`, as COMSOL does."""

    PARAMS = {
        "TemperatureBoundary": {"T0"},
        "HeatFluxBoundary": {"q0", "HeatFluxType", "h", "Text"},
        "Fixed": set(),
        "InletBoundary": {"U0in"},
        "OutletBoundary": {"p0"},
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

    def tag(self):
        return self._tag

    def label(self, new=None):
        if new is not None:
            self._label = new
        return self._label

    def selection(self):
        return self._sel

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

    def getSDim(self):
        return self._sdim

    def run(self):
        self.runs += 1

    def getNBoundaries(self):
        return 6 if self._sdim == 3 else 4

    def getNDomains(self):
        return self._n_domains

    def faceParamRange(self, face):
        _, _, (a, b) = self.FACES[face]
        return [0.0, self.size[a] * 1000, 0.0, self.size[b] * 1000]

    def faceX(self, face, uv):
        axis, at_max, (a, b) = self.FACES[face]
        out = []
        for u, v in uv:
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

    def geom(self, tag=None):
        if tag is None:
            return FakeTagList(self._geom_tags)
        if tag not in self._geom_tags:
            raise RuntimeError(f"Unknown geometry {tag}")
        return self._geom


class FakeSolution:
    def __init__(self, empty: bool):
        self._empty = empty

    def isEmpty(self):
        return self._empty


class FakeJavaModel:
    def __init__(self, components, solutions=None):
        self._components = list(components)
        self._solutions = {t: FakeSolution(e) for t, e in (solutions or {}).items()}

    def component(self, tag=None):
        if tag is None:
            return list(self._components)
        return next(c for c in self._components if c.tag() == tag)

    def sol(self, tag=None):
        if tag is None:
            return FakeTagList(self._solutions)
        return self._solutions[tag]


class FakeModel:
    """Stands in for mph.Model: the code under test only reads .java."""

    def __init__(self, components, solutions=None):
        self.java = FakeJavaModel(components, solutions)
