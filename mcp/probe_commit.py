# -*- coding: utf-8 -*-
u"""probe v6: reflection ONLY. No transaction, no scope.Start, no geometry.
Answers: StairsEditScope.Commit / Start signatures in Revit 2019."""
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
from bridge_client import call  # noqa: E402

ID = [3000]


def run(code, label):
    ID[0] += 1
    r = call({"type": "execute_code", "id": ID[0], "code": code,
              "no_transaction": True}, timeout=60)
    res = r.get("result", r)
    print(u"=== %s ===" % label)
    print(json.dumps(res, ensure_ascii=False)[:3000])
    return res


CODE = r'''import clr
import Autodesk.Revit.DB as DB
out = {}
st = clr.GetClrType(DB.StairsEditScope)
out["scope_type"] = st.FullName
sigs = []
for m in st.GetMethods():
    if m.Name in ("Commit", "Cancel", "Start", "Dispose"):
        ps = [str(p.ParameterType) for p in m.GetParameters()]
        sigs.append("%s(%s)" % (m.Name, ",".join(ps)))
out["scope_sigs"] = sigs
tt = clr.GetClrType(DB.Transaction)
tsigs = []
for m in tt.GetMethods():
    if m.Name in ("Commit", "Start"):
        ps = [str(p.ParameterType) for p in m.GetParameters()]
        tsigs.append("%s(%s)" % (m.Name, ",".join(ps)))
out["txn_sigs"] = tsigs
_result = out
'''

run(CODE, u"v6 纯反射 Commit 签名")
