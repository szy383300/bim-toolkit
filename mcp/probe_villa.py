# -*- coding: utf-8 -*-
u"""stairs recon v5: landing gap/order variants (cancel-safe) + no-landing
commit test with failure preprocessor. Highest-risk test runs LAST."""
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
from bridge_client import call  # noqa: E402

ID = [2000]


def run(code, label):
    ID[0] += 1
    r = call({"type": "execute_code", "id": ID[0], "code": code,
              "no_transaction": True}, timeout=180)
    res = r.get("result", r)
    print(u"=== %s ===" % label)
    print(json.dumps(res, ensure_ascii=False)[:2400])
    return res


TMPL = r'''# -*- coding: utf-8 -*-
import Autodesk.Revit.DB as DB
import Autodesk.Revit.DB.Architecture as ARCH
from Autodesk.Revit.DB import IFailuresPreprocessor, FailureProcessingResult
from System import Enum as _Enum
import clr
out = {}

class Pre(IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        try:
            ms = fa.GetFailureMessages()
            if ms.Count:
                fa.ResolveFailures(ms)
                return FailureProcessingResult.ProceedWithCommit
        except Exception:
            pass
        return FailureProcessingResult.Continue

lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
bl, tl = lvls[0], lvls[1]
ft = 0.00328084
just = list(_Enum.GetValues(clr.GetClrType(
    ARCH.StairsRunJustification)))[1]
__BODY__
_result = out
'''

GAP_VARIANTS = r'''scope = DB.StairsEditScope(doc, "recon v5 gaps")
sid = scope.Start(bl.Id, tl.Id)
t = Transaction(doc, "recon v5")
fo = t.GetFailureHandlingOptions()
fo.SetFailuresPreprocessor(Pre())
t.SetFailureHandlingOptions(fo)
t.Start()
out["tried"] = []
made = None
for gap in (250, 500, 1000, 1200):
    try:
        r1 = ARCH.StairsRun.CreateStraightRun(
            doc, sid,
            Line.CreateBound(XYZ(600 * ft, 0, bl.Elevation),
                             XYZ(600 * ft, 1800 * ft, bl.Elevation)), just)
        r2 = ARCH.StairsRun.CreateStraightRun(
            doc, sid,
            Line.CreateBound(XYZ(1800 * ft, (1800 + gap) * ft, bl.Elevation),
                             XYZ(1800 * ft, gap * ft, bl.Elevation)), just)
        lands = ARCH.StairsLanding.CreateAutomaticLanding(
            doc, r1.Id, r2.Id)
        made = {"gap": gap,
                "landings": [int(i.IntegerValue) for i in lands]}
        break
    except Exception as ex:
        out["tried"].append("%d: %s" % (gap, to_text(ex)[:80]))
        try:
            t.RollBack()
            t.Start()
        except Exception:
            break
out["made"] = made
if made:
    t.Commit()
    scope.Commit()
    out["committed"] = True
else:
    try:
        t.RollBack()
    except Exception:
        pass
    try:
        scope.Cancel()
        out["cancelled"] = True
    except Exception as ex2:
        out["cancel_err"] = to_text(ex2)[:100]
'''

NO_LANDING_COMMIT = r'''scope = DB.StairsEditScope(doc, "recon v5 noland")
sid = scope.Start(bl.Id, tl.Id)
t = Transaction(doc, "recon v5 noland")
fo = t.GetFailureHandlingOptions()
fo.SetFailuresPreprocessor(Pre())
t.SetFailureHandlingOptions(fo)
t.Start()
try:
    r1 = ARCH.StairsRun.CreateStraightRun(
        doc, sid,
        Line.CreateBound(XYZ(600 * ft, 3300 * ft, bl.Elevation),
                         XYZ(600 * ft, 5100 * ft, bl.Elevation)), just)
    out["run1"] = int(r1.Id.IntegerValue)
    r2 = ARCH.StairsRun.CreateStraightRun(
        doc, sid,
        Line.CreateBound(XYZ(1800 * ft, 6300 * ft, bl.Elevation),
                         XYZ(1800 * ft, 4500 * ft, bl.Elevation)), just)
    out["run2"] = int(r2.Id.IntegerValue)
    t.Commit()
    out["txn_ok"] = True
    scope.Commit()
    out["scope_ok"] = True
    st = doc.GetElement(sid)
    try:
        out["auto_landings"] = len(list(ARCH.Stairs.GetLandings(st)))
    except Exception as ex:
        out["land_probe"] = to_text(ex)[:80]
except Exception as ex:
    out["err"] = to_text(ex)[:200]
    try:
        t.RollBack()
    except Exception:
        pass
    try:
        scope.Cancel()
        out["cancelled"] = True
    except Exception as ex2:
        out["cancel_err"] = to_text(ex2)[:100]
'''

run(TMPL.replace("__BODY__", GAP_VARIANTS), u"v5 平台间隙扫描")
run(TMPL.replace("__BODY__", NO_LANDING_COMMIT), u"v5 无平台直提")
