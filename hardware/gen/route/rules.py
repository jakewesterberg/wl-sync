"""The breakout board's routing rules, read at run time from its rule files (task R1a).

    Rules.load(hardware/breakout) -> Rules

Every width and clearance the engine uses comes from here, and every one of them is read
from `breakout.kicad_dru` (the custom rules: N-1 to N-8 widths, N-12, N-13 and its U11
exemption, N-16, S-5) and `breakout.kicad_pro` (the net classes, their patterns, and the
board's design-rule floors). Nothing is copied by hand, so a rule changed in those files
changes the router (plan, Review Focus 2). Pure Python: no numpy and no pcbnew, so KiCad's
Python can import it too.

How the custom rules are read. The .kicad_dru is parsed as s-expressions, and each rule's
condition is compiled from KiCad's expression language into a Python predicate over two
item descriptions, A and B (`Item`). Only the forms this board's rules use are understood:
`||`, `&&`, `!`, parentheses, `X.NetName == 'n'`, `X.NetClass == 'c'`, `X.Type == 't'`,
`X.Pad_Type == 't'`, `X.Pad_Number == 'n'`, `A.Net != B.Net` (and `==`),
`X.memberOfFootprint('R')`, `X.existsOnLayer('L')` and `X.intersectsCourtyard('R')`.
Anything else makes `load` raise, so a new kind of rule cannot be silently ignored.

KiCad's resolution is followed: for one constraint type, the last rule in the file whose
layer and condition match (with A and B either way round) wins; with none, the net
classes' value (the larger of the two for a clearance); and never below the board's floor
(`min_clearance`, `min_track_width`).

Board-level clearances that the .kicad_pro leaves at KiCad's defaults: copper to the board
edge 0.5 mm, copper to a hole 0.25 mm, hole to hole 0.25 mm. These are KiCad 10.0.5's
defaults for `min_copper_edge_clearance`, `min_hole_clearance` and `min_hole_to_hole`
(pcbnew reports exactly these on breakout.kicad_pcb, whose .kicad_pro sets none of them);
a value in the .kicad_pro replaces them. `board_io.extract()` records the board's own
values in the obstacle file, and `Grid.add_obstacles` refuses a file that disagrees.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

LAYERS = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")
OUTER = frozenset({"F.Cu", "B.Cu"})
INNER = frozenset({"In1.Cu", "In2.Cu"})

# KiCad 10.0.5's defaults for the three board-level rules breakout.kicad_pro does not set
# (see the module docstring); a value in the .kicad_pro's design_settings.rules wins.
KICAD_DEFAULTS = {"min_copper_edge_clearance": 0.5, "min_hole_clearance": 0.25,
                  "min_hole_to_hole": 0.25, "min_clearance": 0.0, "min_track_width": 0.0}


class RulesError(ValueError):
    """A rule file holds something this reader does not understand."""


@dataclass(frozen=True)
class Item:
    """One side (A or B) of a rule's condition: a copper item as KiCad's DRC sees it.

    `courtyards` is the set of footprints whose courtyard the item intersects. The engine
    asks about tracks it has not drawn yet, so it passes none: S-5's courtyard rules relax
    a width, and leaving them out can only make a track wider than it need be."""
    type: str = "Track"             # 'Track', 'Via' or 'Pad'
    net: str = ""
    netclass: str = "Default"
    footprint: str = ""             # a pad's footprint reference
    pad_type: str = ""              # 'SMD', 'Through-hole', 'NPTH, mechanical', ...
    pad_number: str = ""
    layers: frozenset = frozenset(LAYERS)
    courtyards: frozenset = frozenset()


# ---- s-expressions ------------------------------------------------------------------------
_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


def _sexpr(text: str):
    """Parse s-expressions (comments start with '#' at a line's start). Strings stay
    strings, wrapped in a 1-tuple so they can be told from bare atoms."""
    text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    pos, stack, out = 0, [[]], None
    while True:
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            if text[pos:].strip():
                raise RulesError(f"cannot parse rule file at {text[pos:pos + 40]!r}")
            break
        pos = m.end()
        if m.group(1):
            stack.append([])
        elif m.group(2):
            done = stack.pop()
            stack[-1].append(done)
        elif m.group(3) is not None:
            stack[-1].append((m.group(3),))
        else:
            stack[-1].append(m.group(4))
    if len(stack) != 1:
        raise RulesError("unbalanced parentheses in rule file")
    out = stack[0]
    return out


def _mm(v: str) -> float:
    m = re.fullmatch(r"([-\d.]+)\s*(mm)?", v)
    if not m:
        raise RulesError(f"not a length in mm: {v!r}")
    return float(m.group(1))


# ---- KiCad's condition language, the subset this board uses --------------------------------
_CTOK = re.compile(r"\s*(\|\||&&|!=|==|!|\(|\)|'[^']*'|[AB]\.[A-Za-z_]+)")


def _ctokens(s: str):
    pos, out = 0, []
    s = s.strip()
    while pos < len(s):
        m = _CTOK.match(s, pos)
        if not m:
            raise RulesError(f"condition not understood at {s[pos:pos + 40]!r}")
        out.append(m.group(1))
        pos = m.end()
    return out


_ATTR = {"NetName": "net", "NetClass": "netclass", "Type": "type", "Pad_Type": "pad_type",
         "Pad_Number": "pad_number"}


class _Cond:
    """A compiled condition: a predicate f(A, B) -> bool, plus the net-name literals it
    tests A against (for `Rules.panel_nets`)."""

    def __init__(self, text: str):
        self.text = text
        self.a_names: set[str] = set()
        self.toks = _ctokens(text)
        self.i = 0
        self.f = self._or()
        if self.i != len(self.toks):
            raise RulesError(f"trailing tokens in condition {text!r}")
        del self.toks

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self, want=None):
        t = self.peek()
        if t is None or (want is not None and t != want):
            raise RulesError(f"expected {want!r} in condition {self.text!r}, got {t!r}")
        self.i += 1
        return t

    def _or(self):
        parts = [self._and()]
        while self.peek() == "||":
            self.take()
            parts.append(self._and())
        if len(parts) == 1:
            return parts[0]
        # 129 `A.NetName == '...'` joined by || (N-13): one set lookup instead
        sets = [p for p in parts if isinstance(p, _NameIs)]
        if len(sets) == len(parts) and len({p.side for p in sets}) == 1:
            return _NameIs(sets[0].side, frozenset().union(*(p.names for p in sets)))
        return lambda a, b, ps=tuple(parts): any(p(a, b) for p in ps)

    def _and(self):
        parts = [self._not()]
        while self.peek() == "&&":
            self.take()
            parts.append(self._not())
        if len(parts) == 1:
            return parts[0]
        return lambda a, b, ps=tuple(parts): all(p(a, b) for p in ps)

    def _not(self):
        if self.peek() == "!":
            self.take()
            inner = self._not()
            return lambda a, b: not inner(a, b)
        if self.peek() == "(":
            self.take("(")
            f = self._or()
            self.take(")")
            return f
        return self._atom()

    def _atom(self):
        lhs = self.take()
        m = re.fullmatch(r"([AB])\.([A-Za-z_]+)", lhs or "")
        if not m:
            raise RulesError(f"condition not understood: {lhs!r} in {self.text!r}")
        side, attr = m.groups()
        get = (lambda a, b: a) if side == "A" else (lambda a, b: b)
        if self.peek() == "(":                                   # a function call
            self.take("(")
            arg = self.take()
            self.take(")")
            if not (arg.startswith("'") and arg.endswith("'")):
                raise RulesError(f"expected a quoted argument in {self.text!r}")
            arg = arg[1:-1]
            if attr == "memberOfFootprint":
                return lambda a, b: get(a, b).footprint == arg
            if attr == "existsOnLayer":
                return lambda a, b: arg in get(a, b).layers
            if attr == "intersectsCourtyard":
                return lambda a, b: arg in get(a, b).courtyards
            raise RulesError(f"function {attr} not understood in {self.text!r}")
        op = self.take()
        if op not in ("==", "!="):
            raise RulesError(f"operator {op!r} not understood in {self.text!r}")
        rhs = self.take()
        if attr == "Net":                                         # A.Net != B.Net
            if rhs not in ("A.Net", "B.Net"):
                raise RulesError(f"{lhs} compared with {rhs!r} in {self.text!r}")
            eq = lambda a, b: a.net == b.net                      # noqa: E731
            return eq if op == "==" else (lambda a, b: not eq(a, b))
        if attr not in _ATTR or not (rhs.startswith("'") and rhs.endswith("'")):
            raise RulesError(f"{lhs} {op} {rhs} not understood in {self.text!r}")
        lit = rhs[1:-1]
        if attr == "NetName" and op == "==":
            if side == "A":
                self.a_names.add(lit)
            return _NameIs(side, frozenset([lit]))
        key = _ATTR[attr]
        if op == "==":
            return lambda a, b: getattr(get(a, b), key) == lit
        return lambda a, b: getattr(get(a, b), key) != lit


class _NameIs:
    def __init__(self, side, names):
        self.side, self.names = side, names

    def __call__(self, a, b):
        return (a if self.side == "A" else b).net in self.names


@dataclass
class Rule:
    name: str
    layers: frozenset | None          # None: every layer
    cond: _Cond | None
    constraints: dict                 # constraint type -> min, in mm

    def matches(self, a: Item, b: Item, layer: str | None) -> bool:
        if self.layers is not None and layer is not None and layer not in self.layers:
            return False
        if self.cond is None:
            return True
        return self.cond.f(a, b) or self.cond.f(b, a)


def _layer_set(v) -> frozenset:
    name = v[0] if isinstance(v, tuple) else v
    if name == "outer":
        return OUTER
    if name == "inner":
        return INNER
    if name in LAYERS:
        return frozenset([name])
    raise RulesError(f"layer {name!r} not understood")


def parse_dru(text: str) -> list[Rule]:
    rules = []
    for node in _sexpr(text):
        if not isinstance(node, list) or not node:
            raise RulesError(f"unexpected top-level form {node!r}")
        if node[0] == "version":
            continue
        if node[0] != "rule":
            raise RulesError(f"unexpected top-level form {node[0]!r}")
        name = node[1][0] if isinstance(node[1], tuple) else node[1]
        layers, cond, cons = None, None, {}
        for part in node[2:]:
            head = part[0]
            if head == "layer":
                layers = _layer_set(part[1])
            elif head == "condition":
                cond = _Cond(part[1][0])
            elif head == "constraint":
                ctype = part[1]
                vals = {p[0]: p[1] for p in part[2:] if isinstance(p, list)}
                if "min" not in vals:
                    raise RulesError(f"rule {name!r}: constraint {ctype} has no min")
                cons[ctype] = _mm(vals["min"])
            else:
                raise RulesError(f"rule {name!r}: clause {head!r} not understood")
        rules.append(Rule(name, layers, cond, cons))
    return rules


# ---- the rules --------------------------------------------------------------------------
@dataclass
class Rules:
    netclasses: dict                  # name -> {clearance, track_width, via_diameter, via_drill}
    patterns: list                    # [(netclass, pattern)] in file order
    rules: list                       # [Rule] in file order
    board: dict                       # min_copper_edge_clearance etc., mm
    sha256: str
    source: Path | None = None
    _cache: dict = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, breakout_dir: Path) -> "Rules":
        breakout_dir = Path(breakout_dir)
        dru_p = breakout_dir / "breakout.kicad_dru"
        pro_p = breakout_dir / "breakout.kicad_pro"
        dru_b, pro_b = dru_p.read_bytes(), pro_p.read_bytes()
        pro = json.loads(pro_b)
        ns = pro["net_settings"]
        classes = {}
        for c in ns["classes"]:
            classes[c["name"]] = {k: float(c[k]) for k in
                                  ("clearance", "track_width", "via_diameter", "via_drill")}
        if "Default" not in classes:
            raise RulesError("breakout.kicad_pro has no Default net class")
        patterns = [(p["netclass"], p["pattern"]) for p in (ns.get("netclass_patterns") or [])]
        for nc, _ in patterns:
            if nc not in classes:
                raise RulesError(f"pattern names an unknown net class {nc!r}")
        if ns.get("netclass_assignments"):
            raise RulesError("explicit netclass_assignments are not read; use patterns")
        board = dict(KICAD_DEFAULTS)
        board.update({k: float(v) for k, v in
                      pro.get("board", {}).get("design_settings", {}).get("rules", {}).items()
                      if k in KICAD_DEFAULTS})
        digest = hashlib.sha256()
        for name, data in (("breakout.kicad_dru", dru_b), ("breakout.kicad_pro", pro_b)):
            digest.update(name.encode() + b"\0" + data + b"\0")
        return cls(classes, patterns, parse_dru(dru_b.decode()), board, digest.hexdigest(),
                   breakout_dir)

    # -- net classes --------------------------------------------------------------------------
    def netclass(self, net: str) -> str:
        key = ("nc", net)
        if key not in self._cache:
            nc = "Default"
            for cls_, pat in self.patterns:
                if fnmatch.fnmatchcase(net, pat):
                    nc = cls_
                    break
            self._cache[key] = nc
        return self._cache[key]

    def item(self, net: str, type: str = "Track", **kw) -> Item:
        return Item(type=type, net=net, netclass=self.netclass(net) if net else "Default", **kw)

    def _last(self, ctype: str, a: Item, b: Item, layer: str | None):
        val = None
        for r in self.rules:
            if ctype in r.constraints and r.matches(a, b, layer):
                val = r.constraints[ctype]
        return val

    # -- widths ---------------------------------------------------------------------------------
    def width(self, net: str) -> float:
        """The width the router draws a track of `net` at: its net class's width, and never
        under a track_width rule's minimum (N-1 to N-8) or the board's floor."""
        key = ("w", net)
        if key not in self._cache:
            a = self.item(net)
            w = self.netclasses[self.netclass(net)]["track_width"]
            m = self._last("track_width", a, Item(type="Track"), None)
            self._cache[key] = max(w, m or 0.0, self.board["min_track_width"])
        return self._cache[key]

    # -- clearances ---------------------------------------------------------------------------
    def clearance(self, a: str, b: str, layer: str, a_item: Item | None = None,
                  b_item: Item | None = None) -> float:
        """Copper-to-copper clearance between net a's item and net b's on `layer`. The
        items default to tracks; the engine passes pads and vias as they are. Two items of
        one net (other than no net) need none."""
        if a == b and a:
            return 0.0
        ai = a_item or self.item(a)
        bi = b_item or self.item(b)
        key = ("c", ai, bi, layer)
        hit = self._cache.get(key)
        if hit is None:
            v = self._last("clearance", ai, bi, layer)
            if v is None:
                v = max(self.netclasses[ai.netclass]["clearance"],
                        self.netclasses[bi.netclass]["clearance"])
            hit = max(v, self.board["min_clearance"])
            self._cache[key] = hit
        return hit

    def via_pad_web(self, smd: bool, ref: str = "", num: str = "", net: str = "",
                    layers=("F.Cu",)) -> float:
        """N-16's physical clearance from a via's copper to a pad (any net, its own
        included); 0.0 where no rule asks one."""
        b = Item(type="Pad", net=net, netclass=self.netclass(net) if net else "Default",
                 footprint=ref, pad_number=str(num), pad_type="SMD" if smd else "Through-hole",
                 layers=frozenset(layers))
        key = ("pw", b)
        if key not in self._cache:
            v = self._last("physical_clearance", Item(type="Via"), b, None)
            self._cache[key] = v or 0.0
        return self._cache[key]

    # -- vias and board-level rules ---------------------------------------------------------
    @property
    def via(self) -> tuple[float, float]:
        """(diameter, drill) of every via (N-14): one size for every net class."""
        sizes = {(c["via_diameter"], c["via_drill"]) for c in self.netclasses.values()}
        if len(sizes) != 1:
            raise RulesError(f"net classes disagree on the via: {sorted(sizes)}")
        return next(iter(sizes))

    @property
    def edge_clearance(self) -> float:
        return self.board["min_copper_edge_clearance"]

    @property
    def hole_clearance(self) -> float:
        return self.board["min_hole_clearance"]

    @property
    def hole_to_hole(self) -> float:
        return self.board["min_hole_to_hole"]

    @property
    def panel_nets(self) -> frozenset:
        """N-13's nets: those its outer-layer clearance rule names on the A side."""
        found = [r for r in self.rules if r.name.startswith("N-13 ") and "clearance" in
                 r.constraints and r.cond is not None and r.cond.a_names]
        if len(found) != 1:
            raise RulesError(f"expected one N-13 clearance rule naming nets, found {len(found)}")
        return frozenset(found[0].cond.a_names)

    @property
    def max_clearance(self) -> float:
        vals = [c["clearance"] for c in self.netclasses.values()]
        vals += [r.constraints[k] for r in self.rules for k in ("clearance", "physical_clearance")
                 if k in r.constraints]
        return max(vals)
