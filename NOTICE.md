# NOTICE

本仓库 **dr-workbench** 是一个衍生（derivative）作品，由以下三部分组成。使用、复制或分发本仓库时，请同时遵守各组件的许可证条款。

## 1. jianying-editor 技能本体（`jianying-editor/`）

- **来源**：https://github.com/luoluoluo22/jianying-editor
- **许可证**：MIT License
- **版权**：Copyright (c) 2026 luoluoluo22
- **许可全文**：见 [`jianying-editor/LICENSE`](jianying-editor/LICENSE)（原样保留，未作任何修改）

该目录下的源码为**原样引入**，未做功能性改动；仅剔除了运行时缓存目录
（`assets/__jycache__/`、`data/jy_cached_audio.csv`）以保持仓库干净。

## 2. pyJianYingDraft（`jianying-editor/scripts/vendor/pyJianYingDraft/`）

- **来源**：https://github.com/GuanYixuan/pyJianYingDraft
- **许可证**：Apache License, Version 2.0
- **版权**：Copyright (c) 2024 GuanYixuan
- **许可全文**：见 [`jianying-editor/scripts/vendor/pyJianYingDraft/LICENSE`](jianying-editor/scripts/vendor/pyJianYingDraft/LICENSE)（原样保留）

该模块随 jianying-editor 一同内嵌，本仓库**未做任何修改**。

## 3. 对标复刻工作台（`workbench/`）—— 本仓库新增部分

- **许可证**：MIT License（版权归 dr-workbench 贡献者所有）
- **内容**：`workbench.py`、`reference_analyzer.py`、`replica_builder.py`、`paths.py` 及 `_selftest/`、`specs/` 下的产物

新增部分在运行时通过 `sys.path` 调用上述 jianying-editor / pyJianYingDraft
的能力（剪映草稿生成），**未修改其源码**。原代码中硬编码的本地绝对路径已在
新增部分中参数化（见 `workbench/paths.py`）。

## 修改声明（Apache-2.0 第 4 条 / 通用致谢）

- 对 jianying-editor 源码：**无修改**（仅清理缓存）。
- 对 pyJianYingDraft 源码：**无修改**。
- 新增文件均在文件头注明「本文件为 dr-workbench 新增代码」。

感谢原作者的开源工作：

- [luoluoluo22/jianying-editor](https://github.com/luoluoluo22/jianying-editor) — 剪映自动化技能
- [GuanYixuan/pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft) — 剪映草稿数据映射引擎
