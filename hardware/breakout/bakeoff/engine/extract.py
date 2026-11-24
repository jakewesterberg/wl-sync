"""A board -> the obstacle schema of hardware/gen/route/stage.py (R1a's scratch extractor,
kept for the R1c bake-off until R1b's board_io.extract() replaces it). Run with KiCad's
Python: extract.py BOARD OUT.json"""
import hashlib, json, sys
import pcbnew

LAYERS = [("F.Cu", pcbnew.F_Cu), ("In1.Cu", pcbnew.In1_Cu), ("In2.Cu", pcbnew.In2_Cu), ("B.Cu", pcbnew.B_Cu)]
mm = pcbnew.ToMM
MAXERR = pcbnew.FromMM(0.002)


def chain(c):
    return [[round(mm(c.CPoint(k).x), 6), round(mm(c.CPoint(k).y), 6)] for k in range(c.PointCount())]


def main(src, out):
    b = pcbnew.LoadBoard(src)
    ds = b.GetDesignSettings()
    obs = {"schema": "wl-sync-route-obstacles", "version": 1, "units": "mm",
           "board": src.split("/")[-1],
           "placement_sha256": hashlib.sha256(open(src, "rb").read()).hexdigest(),
           "layers": [n for n, _ in LAYERS],
           "design": {"min_copper_edge_clearance": mm(ds.m_CopperEdgeClearance),
                      "min_hole_clearance": mm(ds.m_HoleClearance),
                      "min_hole_to_hole": mm(ds.m_HoleToHoleMin)},
           "pads": [], "holes": [], "keepouts": [], "tracks": [], "vias": []}
    ol = pcbnew.SHAPE_POLY_SET()
    b.GetBoardPolygonOutlines(ol, False)
    assert ol.OutlineCount() == 1, ol.OutlineCount()
    obs["outline"] = {"outer": chain(ol.Outline(0)),
                      "holes": [chain(ol.Hole(0, h)) for h in range(ol.HoleCount(0))]}
    for fp in b.GetFootprints():
        for p in fp.Pads():
            attr = p.GetAttribute()
            pos = p.GetPosition()
            at = [round(mm(pos.x), 6), round(mm(pos.y), 6)]
            ds_ = p.GetDrillSize()
            if attr in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH) and ds_.x > 0:
                obs["holes"].append({"at": at, "diameter": round(mm(max(ds_.x, ds_.y)), 6),
                                     "plated": attr == pcbnew.PAD_ATTRIB_PTH,
                                     "net": p.GetNetname() if attr == pcbnew.PAD_ATTRIB_PTH else ""})
            lays = [n for n, lid in LAYERS if p.IsOnLayer(lid) and p.FlashLayer(lid)]
            if attr == pcbnew.PAD_ATTRIB_NPTH or not lays:
                continue
            lid = dict(LAYERS)[lays[0]]
            poly = pcbnew.SHAPE_POLY_SET()
            p.TransformShapeToPolygon(poly, lid, 0, MAXERR, pcbnew.ERROR_OUTSIDE)
            poly.Simplify()
            assert poly.OutlineCount() == 1, (fp.GetReference(), p.GetNumber(), poly.OutlineCount())
            obs["pads"].append({"ref": fp.GetReference(), "num": p.GetNumber(), "net": p.GetNetname(),
                                "layers": lays, "smd": attr in (pcbnew.PAD_ATTRIB_SMD, pcbnew.PAD_ATTRIB_CONN),
                                "at": at, "polygon": chain(poly.Outline(0))})
    for z in b.Zones():
        if not z.GetIsRuleArea():
            continue
        f = [k for k, on in (("tracks", z.GetDoNotAllowTracks()), ("vias", z.GetDoNotAllowVias()),
                             ("pours", z.GetDoNotAllowZoneFills())) if on]
        so = z.Outline()
        for i in range(so.OutlineCount()):
            obs["keepouts"].append({"name": z.GetZoneName(), "forbids": f,
                                    "layers": [n for n, lid in LAYERS if z.GetLayerSet().Contains(lid)],
                                    "polygon": chain(so.Outline(i))})
    for t in b.GetTracks():
        if t.GetClass() == "PCB_VIA":
            obs["vias"].append({"net": t.GetNetname(), "at": [mm(t.GetPosition().x), mm(t.GetPosition().y)],
                                "size": mm(t.GetWidth(pcbnew.F_Cu)), "drill": mm(t.GetDrillValue())})
        elif t.GetClass() == "PCB_TRACK":
            obs["tracks"].append({"net": t.GetNetname(), "layer": b.GetLayerName(t.GetLayer()),
                                  "width": mm(t.GetWidth()), "pts": [[mm(t.GetStart().x), mm(t.GetStart().y)],
                                                                     [mm(t.GetEnd().x), mm(t.GetEnd().y)]]})
        else:
            raise SystemExit("arc tracks not handled: " + t.GetClass())
    json.dump(obs, open(out, "w"), indent=0)
    print("pads", len(obs["pads"]), "holes", len(obs["holes"]), "keepouts", len(obs["keepouts"]),
          "cut-outs", len(obs["outline"]["holes"]))


main(sys.argv[1], sys.argv[2])
