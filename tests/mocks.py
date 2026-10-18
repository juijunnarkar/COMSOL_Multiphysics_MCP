"""Small stand-ins for the COMSOL Java API, for tests that run without COMSOL.

Each class copies only the calls that the code under test makes, with the
same names as the Java API (component(), material(tag), selection(), ...).
"""


class FakeSelection:
    """A domain selection. Domains above n_domains are dropped, as COMSOL does."""

    def __init__(self, n_domains: int, sdim: int):
        self.n_domains = n_domains
        self.sdim = sdim
        self.domains: list[int] = []
        self.calls: list[str] = []

    def set(self, domains):
        self.calls.append("set")
        self.domains = [d for d in domains if 1 <= d <= self.n_domains]

    def all(self):
        self.calls.append("all")
        self.domains = list(range(1, self.n_domains + 1))

    def entities(self, dim):
        if dim != self.sdim:
            raise RuntimeError(f"No entities at dimension {dim}")
        return list(self.domains)


class FakeMaterial:
    def __init__(self, label: str, sel: FakeSelection):
        self._label = label
        self._sel = sel

    def label(self):
        return self._label

    def selection(self):
        return self._sel


class FakeTagList:
    def __init__(self, tags):
        self._tags = list(tags)

    def tags(self):
        return list(self._tags)


class FakeGeom:
    def __init__(self, sdim: int):
        self._sdim = sdim

    def getSDim(self):
        return self._sdim


class FakeComponent:
    def __init__(self, tag, materials: dict, physics=(), n_domains=3, sdim=3):
        self._tag = tag
        self._physics = list(physics)
        self._sdim = sdim
        self._materials = {
            mtag: FakeMaterial(label, FakeSelection(n_domains, sdim))
            for mtag, label in materials.items()
        }

    def tag(self):
        return self._tag

    def material(self, tag=None):
        if tag is None:
            return FakeTagList(self._materials)
        return self._materials[tag]

    def physics(self):
        return FakeTagList(self._physics)

    def geom(self, tag=None):
        if tag is None:
            return FakeTagList(["geom1"])
        return FakeGeom(self._sdim)


class FakeJavaModel:
    def __init__(self, components):
        self._components = list(components)

    def component(self, tag=None):
        if tag is None:
            return list(self._components)
        return next(c for c in self._components if c.tag() == tag)


class FakeModel:
    """Stands in for mph.Model: the code under test only reads .java."""

    def __init__(self, components):
        self.java = FakeJavaModel(components)
