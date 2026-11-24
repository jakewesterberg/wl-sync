"""A route file onto a placed board, zones filled (R1a's scratch applier, kept for the R1c
bake-off until R1b's board_io replaces it). KiCad's Python:
apply.py PLACED.kicad_pcb ROUTE.json OUT.kicad_pcb"""
import json, os, sys
import pcbnew

nm = pcbnew.FromMM
LID = {"F.Cu": pcbnew.F_Cu, "In1.Cu": pcbnew.In1_Cu, "In2.Cu": pcbnew.In2_Cu, "B.Cu": pcbnew.B_Cu}
CONNECT = {"thermal": pcbnew.ZONE_CONNECTION_THERMAL, "solid": pcbnew.ZONE_CONNECTION_FULL,
           "none": pcbnew.ZONE_CONNECTION_NONE, "tht_thermal": pcbnew.ZONE_CONNECTION_THT_THERMAL}

src, route, dst = sys.argv[1:4]
pcbnew.KIID.SeedGenerator(20261124)
b = pcbnew.LoadBoard(src)
r = json.load(open(route))
for t in r["tracks"] + [f["track"] for f in r["fixed"] if "track" in f]:
    net = b.FindNet(t["net"])
    for (ax, ay), (bx, by) in zip(t["pts"], t["pts"][1:]):
        s = pcbnew.PCB_TRACK(b)
        s.SetStart(pcbnew.VECTOR2I(nm(ax), nm(ay)))
        s.SetEnd(pcbnew.VECTOR2I(nm(bx), nm(by)))
        s.SetWidth(nm(t["width"]))
        s.SetLayer(LID[t["layer"]])
        s.SetNet(net)
        b.Add(s)
for v in r["vias"] + [f["via"] for f in r["fixed"] if "via" in f]:
    o = pcbnew.PCB_VIA(b)
    o.SetPosition(pcbnew.VECTOR2I(nm(v["at"][0]), nm(v["at"][1])))
    o.SetViaType(pcbnew.VIATYPE_THROUGH)
    o.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    o.SetWidth(nm(v["size"]))
    o.SetDrill(nm(v["drill"]))
    o.SetNet(b.FindNet(v["net"]))
    b.Add(o)
for z in r["zones"]:
    zz = pcbnew.ZONE(b)
    zz.SetLayer(LID[z["layer"]])
    zz.SetNet(b.FindNet(z["net"]))
    zz.SetZoneName(z["name"])
    o = zz.Outline()
    o.NewOutline()
    for x, y in z["outline"]:
        o.Append(nm(x), nm(y))
    if "clearance" in z:
        zz.SetLocalClearance(nm(z["clearance"]))
    zz.SetMinThickness(nm(z["min_width"]))
    zz.SetPadConnection(CONNECT[z["connect"]])
    if "thermal_gap" in z:
        zz.SetThermalReliefGap(nm(z["thermal_gap"]))
    if "spoke_width" in z:
        zz.SetThermalReliefSpokeWidth(nm(z["spoke_width"]))
    zz.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    zz.SetAssignedPriority(z["priority"])
    b.Add(zz)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
base = os.path.splitext(dst)[0]
kept = {e: (open(base + e, "rb").read() if os.path.exists(base + e) else None) for e in (".kicad_pro", ".kicad_prl")}
b.Save(dst)
for e, data in kept.items():
    if data is None:
        if os.path.exists(base + e):
            os.remove(base + e)
    else:
        open(base + e, "wb").write(data)
b.BuildConnectivity()
print("saved", dst, "tracks", len(r["tracks"]), "vias", len(r["vias"]), "zones", len(r["zones"]),
      "unconnected", b.GetConnectivity().GetUnconnectedCount(False))
