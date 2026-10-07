# -*- coding: utf-8 -*-
u"""play_4d.py —— 把导览任务树"放"出来：逐步隔离 + 取景 + 导出帧。

它做的事
--------
读 `schedule_to_steps.py` 生成的 `*_4d_steps.json`，对每一步调用桥的
`apply_step`（按 levels 隔离构件并缩放取景），再调 `export_view` 导出一张 JPG。
跑完就是一组可以拼成 4D 动画的帧。

    schedule_summary.csv --[进度关联 按钮]--> *_4d_steps.json --[本脚本]--> frames/step_NN.jpg

用法
----
    <python> mcp/play_4d.py steps.json --out frames
    <python> mcp/play_4d.py steps.json --dry            # 只列步骤，不碰 Revit
    <python> mcp/play_4d.py steps.json --start 3 --end 6
    <python> mcp/play_4d.py steps.json --sleep 1.5      # 每步之间等一会儿（看动画用）

前置：**Revit 已打开、且已点过 MCP Bridge 按钮**（否则连不上桥，退出码 2）。
依赖：纯标准库 + genbuild.bridge（复用它的 token 定位与认证）。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BRIDGE = 2
EXIT_RUNTIME = 3


def load_tree(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    return json.loads(raw.decode("utf-8"))


def step_payload(step):
    u"""把步骤裁剪成桥要的形状（只留它认识的字段）。"""
    p = {"show_all": bool(step.get("show_all"))}
    if not p["show_all"]:
        p["levels"] = list(step.get("levels") or [])
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description=u"4D 导览播放：逐步隔离取景并导出帧")
    ap.add_argument("steps", help=u"导览任务树 JSON（schedule_to_steps 生成）")
    ap.add_argument("--out", default=None, help=u"帧输出目录，默认 <json同级>/frames")
    ap.add_argument("--start", type=int, default=None, help=u"从第几步开始（1 起）")
    ap.add_argument("--end", type=int, default=None, help=u"到第几步结束（含）")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help=u"每步之间的等待秒数（0=尽快跑完）")
    ap.add_argument("--pad-mm", type=float, default=15000.0, help=u"取景外扩（毫米）")
    ap.add_argument("--dry", action="store_true", help=u"只列步骤，不连接 Revit")
    ap.add_argument("--no-export", action="store_true", help=u"只切换步骤，不导出图片")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.steps):
        print(u"[FAIL] 找不到步骤文件: %s" % args.steps)
        return EXIT_USAGE

    try:
        tree = load_tree(args.steps)
    except Exception as e:
        print(u"[FAIL] 解析步骤 JSON 失败: %s" % e)
        return EXIT_USAGE

    steps = tree.get("steps") or []
    if not steps:
        print(u"[FAIL] 步骤文件里没有 steps")
        return EXIT_USAGE

    print(u"导览：%s" % tree.get("task", u"(未命名)"))
    print(u"模型：%s" % (tree.get("model") or u"(未记录)"))
    print(u"步骤：%d 步" % len(steps))
    print(u"")

    lo = args.start or 1
    hi = args.end or steps[-1].get("n", len(steps))
    picked = [s for s in steps if lo <= (s.get("n") or 0) <= hi]
    if not picked:
        print(u"[FAIL] 区间 %s~%s 内没有步骤" % (lo, hi))
        return EXIT_USAGE

    for s in picked:
        scope = u"全楼" if s.get("show_all") else u"/".join(s.get("levels") or []) or u"(空)"
        print(u"  第 %2d 步  [%-14s] %s" % (s.get("n") or 0, scope,
                                            s.get("title") or u""))
    print(u"")

    if args.dry:
        print(u"[dry] 只列步骤，未连接 Revit。")
        return EXIT_OK

    outdir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.steps)),
                                      u"frames")
    if not args.no_export and not os.path.isdir(outdir):
        os.makedirs(outdir)

    try:
        from genbuild.bridge import BridgeClient
    except Exception as e:
        print(u"[FAIL] 无法导入桥客户端: %s" % e)
        return EXIT_RUNTIME

    client = BridgeClient()
    try:
        info = client.call({"type": u"ping"}, timeout=15)
    except Exception as e:
        print(u"[FAIL] 连不上桥：%s" % e)
        print(u"       确认 Revit 已打开，且已点过 MCP Bridge 按钮。")
        return EXIT_BRIDGE
    print(u"桥已连通：文档=%s 版本=%s 策略=%s"
          % (info.get(u"document"), info.get(u"version"), info.get(u"policy")))

    made = []
    for s in picked:
        n = s.get("n") or 0
        try:
            r = client.call({"type": u"apply_step",
                             "step": step_payload(s),
                             "pad_mm": args.pad_mm}, timeout=120)
        except Exception as e:
            print(u"[FAIL] 第 %d 步 apply_step 失败：%s" % (n, e))
            return EXIT_RUNTIME
        if isinstance(r, dict) and r.get("error"):
            print(u"[FAIL] 第 %d 步被拒：%s" % (n, r.get("error")))
            return EXIT_RUNTIME

        frame = u""
        if not args.no_export:
            frame = os.path.join(outdir, u"step_%02d.jpg" % n)
            try:
                er = client.call({"type": u"export_view",
                                  "path": frame.replace(u"\\", u"/")},
                                 timeout=120)
                if isinstance(er, dict) and er.get("error"):
                    print(u"  [WARN] 第 %d 步导图失败：%s" % (n, er.get("error")))
                    frame = u""
            except Exception as e:
                print(u"  [WARN] 第 %d 步导图异常：%s" % (n, e))
                frame = u""

        made.append(frame or (u"(未导图) 第 %d 步" % n))
        print(u"  [OK] 第 %2d 步  %s%s"
              % (n, s.get("title") or u"", (u"  -> " + os.path.basename(frame)) if frame else u""))
        if args.sleep > 0:
            time.sleep(args.sleep)

    print(u"")
    print(u"完成：%d 步。" % len(picked))
    if not args.no_export:
        print(u"帧目录：%s" % outdir)
        print(u"拼成动画（本机 ffmpeg 可用时）：")
        print(u'  ffmpeg -framerate 2 -i "%s\\step_%%02d.jpg" -c:v libx264 -pix_fmt yuv420p 4d.mp4'
              % outdir)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
