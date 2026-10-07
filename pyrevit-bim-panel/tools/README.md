# tools/ —— 这个目录为什么是空的

这个目录**原本放着一颗第三方二进制** `Xceed.Wpf.AvalonDock.dll`（410 KB），给
`../fix_pyrevit_dll.bat` 当恢复源。2026-10-07 公开前审的时候把它从仓库里删掉了，原因记录在下面。
脚本本身还在，用法见 `../fix_pyrevit_dll.bat` 头部注释。

## 为什么删

那颗 dll 不是本项目写的东西，是 **Xceed Software Inc.** 的 Extended WPF Toolkit 里的 AvalonDock
组件（`FileVersionInfo` 实测：Company = Xceed Software Inc.，Product = Xceed Extended WPF Toolkit -
AvalonDock，Copyright 2007-2013）。仓库一旦公开，把别人编译好的成品放在这里**就是在分发它**，
而分发第三方程序集的前提是连同它的许可原文一起带上。

那份原文在开发机上取不到，两条路都试过：

- `raw.githubusercontent.com` 取上游 → 连接超时（本机到 GitHub HTTPS 不稳，是已知网络事实）；
- 本机 pyRevit 安装目录（`bin` / `extensions` / `pyrevitlib` / `site-packages`）→ **没有任何
  LICENSE / NOTICE / COPYING 文件**。

所以只剩两个选择：**要么补进条款，要么不分发**。这里选了我能独立成立的那一个——不分发。
理由不是怕出事，而是：一个来路只能证明、条款无法声明的二进制，放进公开作品集里，
读者看到的不是"功能完整"，是"这个人没意识到这是个问题"。

## 要恢复时，怎么确认你拿到的是对的那一份

这个缺陷的特征是**同名不同版本**，光看文件名不算数。实测过的对照：

| 来源 | AssemblyVersion | SHA-256（前 12 位） | 能不能用 |
|---|---|---|---|
| pyRevit 6.5.5 的 `bin\` | **2.0.19.10** | `7a5adc35be91` | ✅ 就是它 |
| Revit 2019 安装目录 | **2.0.19.4** | `890c8956ee68` | ❌ 看着同名同系列，实际是另一个构建 |

完整校验值：`Xceed.Wpf.AvalonDock.dll` / v2.0.19.10 / PublicKeyToken `3e4669d2f30244f4` /
410,112 字节 / SHA-256 `7a5adc35be910c6e865952396564e01bfb2a0517fb66b2308d3cb4921e4d6645`。

第二行那一条是我这次差点走错的路：本来打算让脚本直接从 Revit 目录取，省得仓库内置。
一比才发现 Revit 自带的是 `2.0.19.4`。**所以 `fix_pyrevit_dll.bat` 现在会读你给它的那份的程序集版本号，
对不上就拒绝复制**（要强行越过用 `/force`）。这条分支是实测过的，不是写了就信。

## 去哪取

1. 另一台同版本 pyRevit 机器的 `C:\Program Files\pyRevit-Master\bin\Xceed.Wpf.AvalonDock.dll`；
2. 重装 pyRevit 6.5.5，官方安装包会把它放进 `bin\`；
3. 取到后：`fix_pyrevit_dll.bat "<那份 dll 的完整路径>"`，脚本只往**缺失**的引擎探测目录里补，
   已在位的原文件不动；先加 `/dry` 可以只看会写到哪。

## 如果以后想改回内置

可以，但得先把条款补齐再推：上游许可原文（含它对再分发的要求）放进本目录，
并在根 README 里指一句"本仓库含第三方程序集 X，采用其 L 许可"。两样都有，才叫分发合规。
