# -*- coding: utf-8 -*-
"""验证 deploy.py 的部署后自校验真的能抓出不一致（不是"永远通过"）。

做法：造两个临时目录当 SRC/DST，分别制造 3 类差异，断言都能被报出来。
不碰真实安装目录。
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # pyrevit-bim-panel/
sys.path.insert(0, HERE)
import deploy  # noqa: E402

tmp = tempfile.mkdtemp(prefix="deploy_selfcheck_")
src = os.path.join(tmp, "src")
dst = os.path.join(tmp, "dst")
os.makedirs(os.path.join(src, "sub"))
os.makedirs(os.path.join(dst, "sub"))


def w(root, rel, text):
    p = os.path.join(root, rel)
    d = os.path.dirname(p)
    if not os.path.isdir(d):
        os.makedirs(d)
    with open(p, "wb") as f:
        f.write(text.encode("utf-8"))


# 基线的 3 个文件
w(src, "a.py", "same")
w(src, "sub/b.py", "same")
w(src, "sub/c.py", "same")
shutil.copytree(src, dst, dirs_exist_ok=True)

ok, problems = deploy._verify_mirror(src, dst)
print("1) 完全一致时        : ok=%s problems=%d  %s" % (ok, len(problems), problems))
assert ok and not problems, "基线应当通过"

# 差异 ①：DST 少一个文件
os.remove(os.path.join(dst, "sub/b.py"))
ok, problems = deploy._verify_mirror(src, dst)
print("2) DST 缺文件        : ok=%s problems=%d  %s" % (ok, len(problems), problems))
assert not ok and any("缺失" in p for p in problems), "应报缺失"

# 差异 ②：内容不同
shutil.copyfile(os.path.join(src, "sub/b.py"), os.path.join(dst, "sub/b.py"))
w(dst, "a.py", "DIFFERENT")
ok, problems = deploy._verify_mirror(src, dst)
print("3) 内容不同          : ok=%s problems=%d  %s" % (ok, len(problems), problems))
assert not ok and any("内容不同" in p for p in problems), "应报内容不同"

# 差异 ③：DST 多一个文件
shutil.copyfile(os.path.join(src, "a.py"), os.path.join(dst, "a.py"))
w(dst, "orphan.py", "x")
ok, problems = deploy._verify_mirror(src, dst)
print("4) DST 多文件        : ok=%s problems=%d  %s" % (ok, len(problems), problems))
assert not ok and any("多余" in p for p in problems), "应报多余"

# 差异 ④：运行时状态 / version.txt 必须被忽略（不能误报）
w(dst, "bridge.token", "tok")
w(dst, "mcp_audit.jsonl", "{}\n")
w(dst, "version.txt", "stamp")
os.makedirs(os.path.join(dst, "__pycache__"))
w(dst, "__pycache__/x.pyc", "junk")
os.makedirs(os.path.join(dst, "logs_archive"))
w(dst, "logs_archive/old.jsonl", "old")
ok, problems = deploy._verify_mirror(src, dst)
print("5) 排除表生效        : ok=%s problems=%d  %s" % (ok, len(problems), problems))
# 此时只剩 orphan.py 一处
assert not ok and len(problems) == 1 and "orphan.py" in problems[0], \
    "排除表应吃掉 bridge.token/mcp_audit.jsonl/version.txt/__pycache__/logs_archive"

shutil.rmtree(tmp, ignore_errors=True)
print("")
print("RESULT: OK —— 自校验能抓 3 类不一致，且排除表不误报")
