# deconstruct-rebuild-workbench · 对标复刻自动化剪辑工作台

> 喂一个对标视频，它教你拍什么、怎么拍，再用你自己的素材复刻出同款成片。

**deconstruct-rebuild-workbench** 是一个基于开源项目 [jianying-editor](https://github.com/luoluoluo22/jianying-editor)（MIT）二次开发的视频剪辑自动化项目。它在原技能「自然语言驱动剪映」的能力之上，新增了一套 **对标复刻（Replica）流水线**：

```
① 找对标                ② 拆解 + 教你怎么拍               ③ 投喂素材复刻
丢一个参考视频  ──►  自动拆出「几分几秒用了什么」  ──►  按拆解结果喂你的素材
（爆款/教程/Vlog）     + 拍摄分镜建议                    ──►  产出 MP4 成片 + 剪映草稿
```

原技能负责「把时间轴搭进剪映」，本工作台负责回答三个问题：**对标片是怎么剪的？我要拍哪些素材？怎么用我的素材复刻出同款？**

---

## 核心能力

### ① 对标拆解（analyze）

喂一个参考视频，自动输出三份文件：

| 产物 | 内容 |
|---|---|
| `<名>_report.md` | 人话版拆解报告：几分几秒用了什么——镜头切分、转场类型、字幕内容与样式、人声/BGM 分段、滤镜痕迹、节奏画像 |
| `<名>_storyboard.md` | **拍摄建议分镜表**：每一镜拍什么、拍多久、要不要旁白、准备哪些素材，照着拍就能复刻 |
| `<名>_spec.json` | 机器可读的结构化时间轴，复刻引擎的直接输入 |

识别维度：硬切 / 叠化 / 淡入淡出(过黑) / 标题字卡 / 黑场；字幕 OCR（内容、上下位置、字号、颜色、出现时段）；人声与 BGM 分离；灰度、暗调、电影黑边等可量化滤镜特征。

### ② 素材复刻（replicate）

按 spec 还原：画幅与分辨率 → 镜头数与时长 → 转场方式 → 片头/片尾卡 → 字幕样式 → AI 旁白配音（edge-tts，字幕与配音时间轴严格对齐）→ BGM 摆放。

产物：`成品/<名>_复刻.mp4`（H.264 + AAC，双击即播）+ 剪映草稿（重启剪映可见，可继续精修）。

---

## 仓库结构

```
deconstruct-rebuild-workbench/
├─ jianying-editor/          # 原技能本体（MIT，源码原样保留，详见 NOTICE.md）
│  └─ scripts/vendor/pyJianYingDraft/   # 内嵌剪映草稿引擎（Apache-2.0）
└─ workbench/                # 【本项目的核心新增】对标复刻工作台
   ├─ workbench.py           # 总控 CLI：analyze / replicate / auto / menu
   ├─ reference_analyzer.py  # 对标分析引擎（OpenCV + Tesseract + ffmpeg）
   ├─ replica_builder.py     # 复刻引擎（ffmpeg + edge-tts + 剪映草稿）
   ├─ paths.py               # 跨平台工具探测（ffmpeg/tesseract/skill 目录）
   ├─ specs/                 # 分析产物示例
   └─ _selftest/             # 自测 fixtures：合成参考片 + 素材 + 已知真值
```

---

## 快速开始

### 1. 安装依赖

```bash
# Python 依赖（三选一环境均可）
pip install -r jianying-editor/requirements.txt
pip install opencv-python pytesseract edge-tts

# 外部工具（二选一方式装上即可，paths.py 会自动探测）
# ffmpeg：https://ffmpeg.org  （或 WinGet: winget install Gyan.FFmpeg）
# Tesseract + 简体中文包：https://tesseract-ocr.github.io （或 WinGet: winget install UB-Mannheim.TesseractOCR）
```

> 装不上 / 探测不到？用环境变量显式指定，见下表。

### 2. 三条命令

```bat
:: ① 拆解对标视频（产出 specs/ 下三份文件）
python workbench\workbench.py analyze "D:\参考\爆款.mp4"

:: ② 照 storyboard.md 拍好素材后，复刻
python workbench\workbench.py replicate "specs\爆款_spec.json" "D:\我的素材" ^
    --title "我的版本" --narration "第一句旁白。第二句旁白。" --bgm "D:\bgm.mp3"

:: ③ 或者一条龙跑完 ① + ②
python workbench\workbench.py auto "D:\参考\爆款.mp4" "D:\我的素材" --narration "..."
```

`--narration` 接受文本或 txt 文件路径；不给则只出画面+BGM。`--bgm` 不给且 spec 判定有 BGM 时，自动生成 lo-fi 氛围乐垫底。

### 3. 环境变量（可选）

`workbench/paths.py` 按「环境变量 → PATH → 常见安装位置」的顺序自动解析，一般无需配置：

| 变量 | 作用 | 默认 |
|---|---|---|
| `FFMPEG_PATH` / `FFPROBE_PATH` | ffmpeg / ffprobe 路径 | PATH 探测，其次 WinGet Packages |
| `TESSERACT_PATH` | tesseract 路径（OCR 用） | PATH 探测，其次 `Program Files\Tesseract-OCR` |
| `JY_SKILL_DIR` | jianying-editor skill 根目录 | 仓库内 `jianying-editor/` |
| `JY_PYTHON` | 子进程解释器 | 当前 Python |

---

## 已知边界（诚实说明）

- 转场、节奏、字幕、镜头切分较准；**具体滤镜 LUT / 调色参数无法 100% 逆向**，复刻为同风格近似。
- OCR 在高亮彩色 / 强纹理背景上可能个别错字；真实拍摄素材通常更准。
- 音频「人声 vs BGM」为启发式判断，复杂混音可能误判。
- 剪映 11.x 上原技能的 UI 自动导出不可用，故 MP4 走 ffmpeg 渲染；剪映草稿正常生成（手动导出）。
- edge-tts 偶发 429 限流，已内置重试；仍失败会自动跳过该句，不影响成片结构。

## 自测

`workbench/_selftest/` 内置一套合成参考片（已知真值：片头卡 → 画面 → 淡入淡出 → 画面 → 片尾卡，全程 BGM + 字幕）和配套素材：

```bat
python workbench\workbench.py analyze "workbench\_selftest\ref_demo.mp4" --out workbench\specs
python workbench\workbench.py replicate "workbench\specs\ref_demo_spec.json" "workbench\_selftest\footage" --title "自测复刻"
```

可与 `workbench/specs/ref_demo_report.md`（正确拆解结果）对照验证。

---

## 致谢与许可证

本项目是衍生作品，由三部分组成，许可证与原作者如下（完整声明见 [NOTICE.md](NOTICE.md)）：

| 组件 | 许可证 | 作者 |
|---|---|---|
| `jianying-editor/` 技能本体 | MIT | [luoluoluo22](https://github.com/luoluoluo22) |
| `scripts/vendor/pyJianYingDraft/` | Apache-2.0 | [GuanYixuan](https://github.com/GuanYixuan) |
| `workbench/` 对标复刻流水线 | MIT（本仓库新增部分） | deconstruct-rebuild-workbench 贡献者 |

仓库根目录的 `LICENSE` 覆盖 `workbench/` 新增部分；第三方组件的原始 LICENSE 与版权声明在各自目录下原样保留。
