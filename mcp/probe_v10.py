# -*- coding: utf-8 -*-
u"""probe v10: connected runs + explicit CreateAutomaticLanding attempt.
Commit regardless of landing success (v7-B proved it lands). Decide
final stairs recipe: with-landing or landing-less + slab fallback."""
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
from bridge_client import call  # noqa: E402

ID = [7000]


def run(code, label):
    ID[0] += 1
    r = call({"type": "execute_code", "id": ID[0], "code": code,
              "no_transaction": True}, timeout=240)
    res = r.get("result", r)
    print(u"=== %s ===" % label)
    print(json.dumps(res, ensure_ascii=False)[:2600])
    return res


CODE = r'''# -*- coding: utf-8 -*-
import Autodesk.Revit.DB as DB
import Autodesk.Revit.DB.Architecture as ARCH
from Autodesk.Revit.DB import (IFailuresPreprocessor,
                               FailureProcessingResult, FailureSeverity,
                               FailureMessageAccessor)
from System.Collections.Generic import List as DotNetList
out = {}
_seen = []
_passes = []

class Pre(IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        _passes.append(1)
        try:
            ms = list(fa.GetFailureMessages())
        except Exception:
            ms = []
        if ms:
            try:
                _seen.append(" | ".join(
                    to_text(m.GetDescriptionText())[:60] for m in ms))
            except Exception:
                pass
            try:
                errs = DotNetList[FailureMessageAccessor]()
                for m in ms:
                    if m.GetSeverity() == FailureSeverity.Error:
                        errs.Add(m)
                try:
                    fa.DeleteAllWarnings()
                except Exception:
                    pass
                if errs.Count > 0:
                    fa.ResolveFailures(errs)
                    if len(_passes) >= 3:
                        return FailureProcessingResult.ProceedWithRollBack
                    return FailureProcessingResult.ProceedWithCommit
            except Exception:
                pass
        return FailureProcessingResult.Continue

lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
bl, tl = lvls[0], lvls[1]
ft = 0.00328084
just = ARCH.StairsRunJustification.Center
pre = Pre()
out["failures"] = _seen
out["passes"] = _passes

scope = DB.StairsEditScope(doc, "recon v10")
sid = scope.Start(bl.Id, tl.Id)
t = Transaction(doc, "recon v10")
fo = t.GetFailureHandlingOptions()
fo.SetFailuresPreprocessor(pre)
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
        Line.CreateBound(XYZ(1800 * ft, 5100 * ft, bl.Elevation),
                         XYZ(1800 * ft, 3300 * ft, bl.Elevation)), just)
    out["run2"] = int(r2.Id.IntegerValue)
    try:
        lands = ARCH.StairsLanding.CreateAutomaticLanding(
            doc, r1.Id, r2.Id)
        out["landing_created"] = [int(i.IntegerValue) for i in lands]
    except Exception as ex2:
        out["landing_err"] = to_text(ex2)[:150]
    t.Commit()
    out["txn_ok"] = True
    scope.Commit(pre)
    out["scope_ok"] = True
    st = doc.GetElement(sid)
    out["runs"] = len(list(st.GetStairsRuns()))
    out["landings"] = len(list(st.GetStairsLandings()))
except Exception as ex:
    out["err"] = to_text(ex)[:220]
    try:
        t.RollBack()
    except Exception:
        pass
    try:
        scope.Cancel()
        out["cancelled"] = True
    except Exception as ex2:
        out["cancel_err"] = to_text(ex2)[:100]
_result = out
'''

run(CODE, u"v10 相接梯段+显式平台")
