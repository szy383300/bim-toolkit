# -*- coding: utf-8 -*-
u"""stairs recon v7: fixed scope.Commit(IFailuresPreprocessor) per 2019
reflection. Config A = villa target geometry (1200 gap). Config B =
connected runs. Both cancel-safe, preprocessor capped at 3 passes."""
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
from bridge_client import call  # noqa: E402

ID = [4000]


def run(code, label):
    ID[0] += 1
    r = call({"type": "execute_code", "id": ID[0], "code": code,
              "no_transaction": True}, timeout=240)
    res = r.get("result", r)
    print(u"=== %s ===" % label)
    print(json.dumps(res, ensure_ascii=False)[:2600])
    return res


TMPL = r'''# -*- coding: utf-8 -*-
import Autodesk.Revit.DB as DB
import Autodesk.Revit.DB.Architecture as ARCH
from Autodesk.Revit.DB import (IFailuresPreprocessor,
                               FailureProcessingResult, FailureSeverity,
                               FailureMessageAccessor)
from System.Collections.Generic import List as DotNetList
from System import Enum as _Enum
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
                    to_text(m.GetDescriptionText())[:70] for m in ms))
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
__BODY__
_result = out
'''

# A: villa target - run1 up north, run2 back south, 1200 gap at top
BODY_A = r'''scope = DB.StairsEditScope(doc, "recon v7 A")
sid = scope.Start(bl.Id, tl.Id)
t = Transaction(doc, "recon v7 A")
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
        Line.CreateBound(XYZ(1800 * ft, 6300 * ft, bl.Elevation),
                         XYZ(1800 * ft, 4500 * ft, bl.Elevation)), just)
    out["run2"] = int(r2.Id.IntegerValue)
    t.Commit()
    out["txn_ok"] = True
    out["scope_ret"] = to_text(scope.Commit(pre))
    out["scope_ok"] = True
    st = doc.GetElement(sid)
    try:
        out["landings"] = len(list(ARCH.Stairs.GetLandings(st)))
    except Exception as ex:
        out["land_err"] = to_text(ex)[:80]
    try:
        out["runs"] = len(list(ARCH.Stairs.GetRuns(st)))
    except Exception as ex:
        out["runs_err"] = to_text(ex)[:80]
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
'''

# B: connected runs - run2 starts exactly at run1's end line
BODY_B = r'''scope = DB.StairsEditScope(doc, "recon v7 B")
sid = scope.Start(bl.Id, tl.Id)
t = Transaction(doc, "recon v7 B")
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
    t.Commit()
    out["txn_ok"] = True
    out["scope_ret"] = to_text(scope.Commit(pre))
    out["scope_ok"] = True
    st = doc.GetElement(sid)
    try:
        out["landings"] = len(list(ARCH.Stairs.GetLandings(st)))
    except Exception as ex:
        out["land_err"] = to_text(ex)[:80]
    try:
        out["runs"] = len(list(ARCH.Stairs.GetRuns(st)))
    except Exception as ex:
        out["runs_err"] = to_text(ex)[:80]
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
'''

run(TMPL.replace("__BODY__", BODY_A), u"v7-A 目标几何(1200间隙)+scope.Commit(pre)")
run(TMPL.replace("__BODY__", BODY_B), u"v7-B 相接几何+scope.Commit(pre)")
