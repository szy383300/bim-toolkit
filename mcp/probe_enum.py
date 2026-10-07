# -*- coding: utf-8 -*-
u"""probe v8: reflection ONLY - which import path resolves
StairsRunJustification, and is ARCH the same object as DB?"""
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
from bridge_client import call  # noqa: E402

ID = [5000]


def run(code, label):
    ID[0] += 1
    r = call({"type": "execute_code", "id": ID[0], "code": code,
              "no_transaction": True}, timeout=60)
    res = r.get("result", r)
    print(u"=== %s ===" % label)
    print(json.dumps(res, ensure_ascii=False)[:2000])
    return res


CODE = r'''import Autodesk.Revit.DB as DB
import Autodesk.Revit.DB.Architecture as ARCH
out = {}
out["db_is_arch"] = (DB is ARCH)
try:
    from Autodesk.Revit.DB import StairsRunJustification as SRJ1
    out["db_from_import"] = to_text(SRJ1.Center)
except Exception as ex:
    out["db_from_import_err"] = to_text(ex)[:120]
try:
    from Autodesk.Revit.DB.Architecture import StairsRunJustification as SRJ2
    out["arch_from_import"] = to_text(SRJ2.Center)
except Exception as ex:
    out["arch_from_import_err"] = to_text(ex)[:120]
try:
    out["arch_attr"] = to_text(ARCH.StairsRunJustification.Center)
except Exception as ex:
    out["arch_attr_err"] = to_text(ex)[:120]
_result = out
'''

run(CODE, u"v8 枚举解析路径反射")
