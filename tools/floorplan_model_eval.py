# -*- coding: utf-8 -*-
u"""floorplan_model_eval.py —— 平面图 YOLO 权重的**客观评估**工具。

为什么有这个文件，而不是一个"PDF 识别入口"
--------------------------------------------
2026-10-01（S14）要给"只有 PDF 没有 CAD"的场景做识别入口。动手前先量了模型：

| 权重 | 末轮 mAP50 | 在自有 val 集 conf>=0.35 | 最高置信度 |
|---|---|---|---|
| floorplan_v1 | **0.000** | 0 | – |
| floorplan_v3 | 0.179 | 0 | – |
| floorplan_v4 | **0.318** | 0 | 0.221 |
| floorplan_v5（最新） | **0.262** | 0 | 0.087 |

mAP50 ≈ 0.26~0.32 是**严重欠拟合**的模型；在自己的验证集上用 0.35 阈值
**一个都检不出来**。而且**最新版 v5 比 v4 还退化了**。

所以那条路**不是没做，是做不出来** —— 缺的是数据与训练，不是代码。
本工具的作用是：**把这件事变成一个可复现、可复测的数字**，
而不是拍脑袋说"效果不好"。等数据/训练改善后，跑一遍就知道够不够用了。

另外，模型只有 5 类（door/window/column/stair/elevator）且输出**边界框**，
**不含墙**。即使精度上去了，"PDF → 完整模型"也不成立 —— 墙必须来自
DWG 图层几何（见 `DWG翻模` 按钮）。

用法
----
    <workbuddy-python> tools/floorplan_model_eval.py --data _train/ds5
    <workbuddy-python> tools/floorplan_model_eval.py --data _train/ds5 --weights _tools/floorplan_v4.pt
    <workbuddy-python> tools/floorplan_model_eval.py --raster 图纸.pdf --out out/

依赖：ultralytics + opencv（+ PyMuPDF 仅 --raster 需要）。
哪个解释器把依赖装齐了就用哪个，脚本不关心它装在哪。
"""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEIGHTS = {
    "v1": os.path.join(REPO, "_tools", "floorplan_v1.pt"),
    "v3": os.path.join(REPO, "_tools", "floorplan_v3.pt"),
    "v4": os.path.join(REPO, "_tools", "floorplan_v4.pt"),
    "v5": os.path.join(REPO, "_tools", "floorplan_v5.pt"),
}
DEFAULT_WEIGHTS = WEIGHTS["v5"]
CLASSES = [u"door", u"window", u"column", u"stair", u"elevator"]
CLASSES_CN = {u"door": u"门", u"window": u"窗", u"column": u"柱",
              u"stair": u"楼梯", u"elevator": u"电梯"}

# 可用的判据（写死在这里，是为了让"够不够用"有个统一口径）
USABLE_MAP50 = 0.70
MARGINAL_MAP50 = 0.50


def load_gt(label_path, W, H):
    u"""读 YOLO 标签（cls cx cy w h，归一化）→ [{cls, box=(x1,y1,x2,y2)}]。"""
    out = []
    if not os.path.isfile(label_path):
        return out
    with open(label_path, "r") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 5:
                continue
            try:
                c = int(float(parts[0]))
                cx, cy, w, h = [float(v) for v in parts[1:5]]
            except ValueError:
                continue
            out.append({"cls": c,
                        "box": ((cx - w / 2) * W, (cy - h / 2) * H,
                                (cx + w / 2) * W, (cy + h / 2) * H)})
    return out


def iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = ix2 - ix1, iy2 - iy1
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def match(gt, pred, thr=0.5):
    u"""按 IoU 贪心匹配，返回 (tp, fp, fn)。同类才算匹配。"""
    used = [False] * len(pred)
    tp = 0
    for g in gt:
        best, bi = thr, -1
        for i, p in enumerate(pred):
            if used[i] or p["cls"] != g["cls"]:
                continue
            v = iou(g["box"], p["box"])
            if v >= best:
                best, bi = v, i
        if bi >= 0:
            used[bi] = True
            tp += 1
    fp = sum(1 for i in range(len(pred)) if not used[i])
    fn = len(gt) - tp
    return tp, fp, fn


def evaluate(data_dir, weights, confs, imgsz, limit=None):
    import cv2
    from ultralytics import YOLO

    with open(os.path.join(data_dir, "data.yaml"), "r") as f:
        names = {}
        in_names = False
        for line in f:
            if line.startswith("names:"):
                in_names = True
                continue
            if in_names and line.strip():
                k, _, v = line.strip().partition(":")
                try:
                    names[int(k)] = v.strip().strip("'\"")
                except ValueError:
                    pass
    if not names:
        names = dict(enumerate(CLASSES))

    img_dir = os.path.join(data_dir, "images", "val")
    lbl_dir = os.path.join(data_dir, "labels", "val")
    imgs = sorted([p for p in os.listdir(img_dir)
                   if p.lower().endswith((".png", ".jpg", ".jpeg"))])
    if limit:
        imgs = imgs[:limit]
    print(u"数据集 val 图片: %d 张   类别: %s"
          % (len(imgs), ", ".join("%d=%s" % (k, v) for k, v in sorted(names.items()))))

    model = YOLO(weights)
    per_conf = {}
    for conf in confs:
        stats = {"tp": 0, "fp": 0, "fn": 0, "gt": 0, "pred": 0, "imgs_with_pred": 0}
        for fn_ in imgs:
            ip = os.path.join(img_dir, fn_)
            img = cv2.imread(ip)
            if img is None:
                continue
            H, W = img.shape[:2]
            gt = load_gt(os.path.join(lbl_dir, fn_[:-4] + ".txt"), W, H)
            r = model.predict(img, imgsz=imgsz, conf=conf, verbose=False)[0]
            pred = [{"cls": int(b.cls), "box": tuple(float(v) for v in b.xyxy[0])}
                    for b in r.boxes]
            tp, fp, fnc = match(gt, pred)
            stats["tp"] += tp
            stats["fp"] += fp
            stats["fn"] += fnc
            stats["gt"] += len(gt)
            stats["pred"] += len(pred)
            if pred:
                stats["imgs_with_pred"] += 1
        per_conf[conf] = stats
    return imgs, per_conf


def prf(s):
    p = s["tp"] / float(s["tp"] + s["fp"]) if (s["tp"] + s["fp"]) else 0.0
    r = s["tp"] / float(s["tp"] + s["fn"]) if (s["tp"] + s["fn"]) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def verdict(mAP50):
    if mAP50 >= USABLE_MAP50:
        return u"可用", u"可以开始做 PDF/图片识别入口"
    if mAP50 >= MARGINAL_MAP50:
        return u"边缘可用", u"仅在低风险场景试探性使用，必须人工复核每一条检出"
    return u"不可用", (u"未达到可用门槛。**不要**基于它做识别入口 —— "
                       u"需要补数据/改训练，而不是改代码")


def main(argv=None):
    ap = argparse.ArgumentParser(description=u"平面图 YOLO 权重客观评估")
    ap.add_argument("--data", default=None, help=u"数据集目录（含 data.yaml 与 labels/）")
    ap.add_argument("--weights", default=None, help=u"权重路径，默认 v5")
    ap.add_argument("--confs", default="0.05,0.15,0.25,0.35",
                    help=u"逗号分隔的置信度阈值")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--limit", type=int, default=None, help=u"只测前 N 张（调试用）")
    ap.add_argument("--raster", default=None, help=u"（附带能力）把 PDF 首页导出为 PNG")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    if args.raster:
        return do_raster(args.raster, args.out)

    weights = args.weights or DEFAULT_WEIGHTS
    if not os.path.isfile(weights):
        print(u"[FAIL] 找不到权重: %s" % weights)
        return 9
    data = args.data or os.path.join(REPO, "_train", "ds5")
    if not os.path.isdir(data):
        print(u"[FAIL] 找不到数据集: %s" % data)
        return 9

    confs = [float(c) for c in args.confs.split(",") if c.strip()]
    print(u"权重: %s" % weights)
    print(u"数据: %s" % data)
    print(u"")
    imgs, per_conf = evaluate(data, weights, confs, args.imgsz, args.limit)

    print(u"")
    print(u"=== IoU>=0.5 贪心匹配下的表现（注意：这是单阈值近似，不是完整 mAP 积分）===")
    print(u"%-8s %8s %8s %8s %8s %8s %8s %10s"
          % (u"conf", u"GT", u"检出", u"TP", u"FP", u"FN", u"F1", u"有检出图"))
    for c in confs:
        s = per_conf[c]
        p, r, f = prf(s)
        print(u"%-8.2f %8d %8d %8d %8d %8d %8.3f %6d/%d"
              % (c, s["gt"], s["pred"], s["tp"], s["fp"], s["fn"], f,
                 s["imgs_with_pred"], len(imgs)))

    # 取 "训练时记录的 mAP50" 作为主判据；记录不到就**实测**一次。
    # 两者必须分清来源 —— 把实测值标成"训练记录"就是在骗人。
    mAP50 = read_final_map50(weights)
    source = u"训练记录"
    if mAP50 is None:
        print(u"")
        print(u"（找不到 %s 的训练记录 results.csv，改为现场跑一次 ultralytics val）"
              % os.path.splitext(os.path.basename(weights))[0])
        mAP50 = run_val_map50(weights, data)
        source = u"本次实测"
    print(u"")
    if mAP50 is None:
        print(u"结论: 拿不到 mAP50，无法判定")
        return 1
    level, advice = verdict(mAP50)
    print(u"%s mAP50 = %.4f" % (source, mAP50))
    print(u"结论: **%s** —— %s" % (level, advice))
    print(u"")
    print(u"（可用门槛设定为 mAP50 >= %.2f；边缘 %.2f）" % (USABLE_MAP50, MARGINAL_MAP50))
    return 0


def read_final_map50(weights):
    u"""按权重名到 _train/<同名>/results.csv 里读末轮 mAP50。找不到返回 None。"""
    stem = os.path.splitext(os.path.basename(weights))[0]   # 如 floorplan_v5
    cand = os.path.join(REPO, "_train", stem, "results.csv")
    if not os.path.isfile(cand):
        return None
    try:
        with open(cand, "r") as f:
            lines = [ln for ln in f.read().strip().splitlines() if ln.strip()]
        hdr = lines[0].split(",")
        idx = next(i for i, h in enumerate(hdr) if "mAP50(B)" in h)
        return float(lines[-1].split(",")[idx])
    except Exception:
        return None


def run_val_map50(weights, data):
    try:
        from ultralytics import YOLO
        m = YOLO(weights)
        r = m.val(data=os.path.join(data, "data.yaml"),
                  imgsz=1280, verbose=False)
        return float(r.box.map50)
    except Exception as e:
        print(u"val 失败: %s" % e)
        return None


def do_raster(pdf, out):
    u"""附带能力：PDF 首页 → PNG。这部分是**验证过可用的**（与模型无关）。"""
    try:
        import cv2
        import fitz
        import numpy as np
    except Exception as e:
        print(u"[FAIL] 缺少依赖（需要 PyMuPDF + opencv）: %s" % e)
        return 9
    if not os.path.isfile(pdf):
        print(u"[FAIL] 找不到: %s" % pdf)
        return 2
    doc = fitz.open(pdf)
    pg = doc.load_page(0)
    zoom = 200 / 72.0
    pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n)
    img = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    out = out or (os.path.splitext(pdf)[0] + u"_p1.png")
    cv2.imwrite(out, img)
    print(u"已导出: %s  (%dx%d)" % (out, img.shape[1], img.shape[0]))
    doc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
