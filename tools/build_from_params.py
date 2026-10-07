# -*- coding: utf-8 -*-
u"""build_from_params.py —— 用 pyRevit 无头运行，按参数 JSON 建一栋楼并存盘。

**不需要 MCP 桥，也不需要点任何按钮。**

用法（在仓库根目录）：
    set BIM_BUILD_PARAMS=E:\\bim-toolkit\\buildings\\三层社区服务中心.params.json
    set BIM_BUILD_OUT=E:\\bim-toolkit\\models\\三层社区服务中心.rvt
    "C:\\Program Files\\pyRevit-Master\\bin\\pyrevit.exe" run ^
        tools\\build_from_params.py "<任意已存在的 rvt 作为宿主>" --revit=2019 --purge

为什么这样设计
--------------
pyRevit 的 `run` 子命令用日志回放驱动 Revit：启动 → 打开<宿主模型> → 跑本脚本 →
退出。它**只能传脚本与宿主模型两个参数**，所以参数走环境变量。

本脚本自己 `NewProjectDocument(默认模板)` 新建文档，**不碰宿主模型** ——
宿主只是让 Revit 有东西可开的"引子"。宿主建议用一份副本，进一步降低风险。

结果写到 `BIM_BUILD_OUT` 同目录的 `_build_result.json`，方便在 Revit 外面核对
（Revit 的输出窗口我们读不到）。
"""
import json
import os
import sys
import traceback

# ---- 路径与参数 ----
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LIB = os.path.join(REPO, "pyrevit-bim-panel", "BIMToolkit.extension", "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

PARAMS_PATH = os.environ.get(
    "BIM_BUILD_PARAMS",
    os.path.join(REPO, "buildings", u"三层社区服务中心.params.json"))
OUT_PATH = os.environ.get(
    "BIM_BUILD_OUT",
    os.path.join(REPO, "models", u"三层社区服务中心.rvt"))
RESULT_PATH = os.path.join(os.path.dirname(OUT_PATH), u"_build_result.json")

LOG = []

# 启动标记：在任何 Revit API 之前就落一个文件。
# 目的：把「pyRevit 没执行到这个脚本」与「执行了但半路失败」区分开 ——
# 否则失败了只能看到"什么都没有"，根本不知道该查哪一头。
_MARKER = os.path.join(os.path.dirname(OUT_PATH), u"_build_started.txt")
try:
    _d = os.path.dirname(_MARKER)
    if _d and not os.path.isdir(_d):
        os.makedirs(_d)
    with open(_MARKER, "wb") as _f:
        _f.write((u"script reached: %s\nout=%s\nparams=%s\n"
                  % (os.path.abspath(__file__), OUT_PATH,
                     PARAMS_PATH)).encode("utf-8"))
except Exception:
    pass


def log(msg):
    try:
        LOG.append(u"%s" % msg)
    except Exception:
        pass
    print(msg)


def write_result(payload):
    try:
        d = os.path.dirname(RESULT_PATH)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(RESULT_PATH, "wb") as f:
            f.write(json.dumps(payload, ensure_ascii=False,
                               indent=2).encode("utf-8"))
    except Exception as e:
        print(u"[WARN] 写结果文件失败: %s" % e)


def main():
    log(u"=== 无头建模 ===")
    log(u"参数: %s" % PARAMS_PATH)
    log(u"输出: %s" % OUT_PATH)

    if not os.path.isfile(PARAMS_PATH):
        write_result({"ok": False, "stage": "params",
                      "error": u"找不到参数文件: %s" % PARAMS_PATH})
        return 2

    try:
        with open(PARAMS_PATH, "rb") as f:
            raw = f.read()
        if raw[:3] == b"\xef\xbb\xbf":
            raw = raw[3:]
        params = json.loads(raw.decode("utf-8"))
    except Exception as e:
        write_result({"ok": False, "stage": "params_parse",
                      "error": u"%s" % e})
        return 2

    # ---- 新建文档（不碰宿主模型）----
    try:
        uiapp = __revit__
        app = uiapp.Application
        tmpl = u""
        try:
            tmpl = app.DefaultProjectTemplate or u""
        except Exception:
            tmpl = u""
        log(u"默认模板: %s" % (tmpl or u"(空)"))
        if tmpl and os.path.isfile(tmpl):
            doc = app.NewProjectDocument(tmpl)
        else:
            # 模板路径取不到时退回无模板新建
            log(u"[WARN] 取不到默认模板，改用 NewProjectDocument(单位制) 兜底")
            doc = app.NewProjectDocument(
                __import__("Autodesk.Revit.DB",
                           fromlist=["UnitSystem"]).UnitSystem.Metric)
        log(u"新文档: %s" % doc.Title)
    except Exception as e:
        write_result({"ok": False, "stage": "new_document",
                      "error": u"%s" % e,
                      "trace": traceback.format_exc()[:1500]})
        return 3

    # ---- 建模 ----
    try:
        import model_builder
    except Exception as e:
        write_result({"ok": False, "stage": "import_model_builder",
                      "error": u"%s" % e, "lib": LIB})
        return 3

    try:
        stats = model_builder.build_model(doc, params, output_func=log)
    except Exception as e:
        write_result({"ok": False, "stage": "build",
                      "error": u"%s" % e,
                      "trace": traceback.format_exc()[:3000],
                      "log": LOG})
        return 4

    log(u"统计: %s" % json.dumps(stats, ensure_ascii=False))

    # ---- 存盘 ----
    saved = u""
    try:
        d = os.path.dirname(OUT_PATH)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        if os.path.exists(OUT_PATH):
            os.remove(OUT_PATH)
        doc.SaveAs(OUT_PATH)
        saved = OUT_PATH
        log(u"已保存: %s" % OUT_PATH)
    except Exception as e:
        log(u"[ERR] 保存失败: %s" % e)
        write_result({"ok": False, "stage": "save", "error": u"%s" % e,
                      "trace": traceback.format_exc()[:1500],
                      "stats": stats, "log": LOG})
        return 5

    write_result({"ok": True, "params": PARAMS_PATH, "saved": saved,
                  "stats": stats, "log": LOG})
    log(u"=== 完成 ===")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        write_result({"ok": False, "stage": "unhandled",
                      "error": u"%s" % e,
                      "trace": traceback.format_exc()[:2000]})
        sys.exit(9)
