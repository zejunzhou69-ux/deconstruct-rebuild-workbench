# 自动化剪辑工作台 · 对标复刻模式

把「**先找对标 → 再拆解 → 拍哪些素材 → 投喂素材复刻**」做成一条可复用的流水线。
基于已部署的 `jianying-editor` 技能 + ffmpeg + OpenCV + Tesseract，全部开源工具，无需逆向。

---

## 核心流程（三步）

```
① 找对标        ② 拆解                ③ 复刻
丢一个参考视频 → 自动拆出「几分几秒    按拆解结果投喂你的素材
                用了什么」+ 拍摄建议    → 复刻出成片 MP4 + 剪映草稿
```

| 步骤 | 命令 | 产物 |
|---|---|---|
| ① 对标分析 | `python workbench.py analyze 参考.mp4` | `specs/<名>_spec.json`（机器读）+ `<名>_report.md`（人话拆解）+ `<名>_storyboard.md`（拍摄建议） |
| ② 照表拍素材 | 看 `storyboard.md` 拍/找素材 | 你自己的素材文件夹 |
| ③ 复刻 | `python workbench.py replicate spec.json 素材文件夹 --narration "..."` | `成品/<名>_复刻.mp4` + 剪映草稿 |

> 也可以 **`python workbench.py auto 参考.mp4 素材文件夹 --narration "..."`** 一条命令跑完 ①+③。

---

## 三个子命令详解

### 1) analyze —— 对标拆解（含拍摄建议）

```bat
python workbench.py analyze "D:\参考\爆款.mp4" --out specs
```

自动识别并输出：

- **镜头切分**：几分几秒一个镜头、每镜多长（画面 / 标题字卡 / 黑场）
- **转场类型**：硬切 / 叠化 / 淡入淡出(过黑)
- **字幕**：OCR 识别文字内容、出现时段、位置（上/中/下）、字号、颜色
- **音频**：人声旁白 / BGM / 静音分段（ZCR + 能量启发式）
- **滤镜痕迹**：灰度 / 暗调 / 高亮 / 电影黑边
- **节奏画像**：快剪/中速/慢节奏、平均镜头时长、横竖屏、有无片头片尾卡
- **拍摄建议（storyboard.md）**：分镜表告诉你每镜拍什么、拍多久、要不要旁白、准备哪些素材

### 2) replicate —— 投喂素材复刻

```bat
python workbench.py replicate "specs\爆款_spec.json" "D:\我的素材" ^
    --title "我的版本" ^
    --narration "第一句旁白。第二句旁白。第三句。" ^
    --bgm "D:\音乐\bgm.mp3" ^
    --out 成品
```

复刻还原的维度：

| 维度 | 复刻方式 |
|---|---|
| 画幅/分辨率 | 按 spec（竖屏 1080×1920 / 横屏 1920×1080） |
| 镜头数与时长 | 按 spec 的 footage 镜头序列（素材循环使用，不足自动循环） |
| 转场 | 硬切=直接切；叠化=xfade；淡入淡出(过黑)=段尾/段首淡出淡入 |
| 片头/片尾卡 | 按 spec 自动生成（标题用 `--title`，片尾默认"感谢观看"可用 `--end` 改） |
| 字幕样式 | 位置/字号/颜色/描边按 OCR 结果还原 |
| 旁白 | `--narration` 文案自动拆句 → edge-tts AI 配音 → 字幕与配音时间轴严格对齐 |
| BGM | 按 spec 判断有无 BGM；有则用 `--bgm` 或自动生成 lo-fi 氛围乐垫底 |

产物：
- `成品/<名>_复刻.mp4` —— H.264 + AAC，可双击播放
- 剪映草稿「标题」—— 重启剪映可见，可继续精修（自动导出按钮版本不兼容问题见下）

### 3) auto / menu

```bat
python workbench.py auto "D:\参考\爆款.mp4" "D:\我的素材" --narration "..."   :: 一条龙
python workbench.py menu                                                        :: 交互式
```

---

## 自然语言用法（最省事）

在 WorkBuddy 会话里直接说：

- “**对标分析**这个视频 `D:\参考\爆款.mp4`”
- “按 `D:\参考\爆款.mp4` 的手法**复刻**，素材在 `D:\我的素材`，标题‘周末露营记’，旁白：今天天气真好……”
- “这个参考片**该怎么拍**？”（我会把 storyboard 分镜表讲给你听）

---

## 文件结构

```
剪辑工作台/
├─ workbench.py           总控（analyze / replicate / auto / menu）
├─ reference_analyzer.py  对标分析引擎（OpenCV + Tesseract + ffmpeg）
├─ replica_builder.py     复刻引擎（ffmpeg + edge-tts + 剪映草稿）
├─ specs/                 分析产物（spec.json + report.md + storyboard.md）
├─ 成品/                  复刻产物（mp4）
└─ _selftest/             自测 fixtures（合成参考片 + 素材，可删）
```

---

## 已知边界（诚实说明）

- **转场/节奏/字幕/镜头切分**较准；**具体滤镜 LUT、调色参数**无法 100% 逆向，复刻为同风格近似。
- OCR 在**高亮彩色/强纹理背景**上可能个别错字（自测中最差情况 5/7 字对）；真实拍摄素材通常更准。
- 音频的「人声 vs BGM」是启发式判断，复杂混音可能误判。
- 本机剪映为 **11.4.2**，`jianying-editor` 的 UI 自动导出只支持 ≤6，故 MP4 走 ffmpeg 渲染；剪映草稿可用（手动导出）。
- edge-tts 偶发 429 限流，已内置重试；仍失败会自动跳过该句（不影响成片结构）。

## 自测

`_selftest/` 里有一套合成参考片（已知真值：片头卡→画面①→淡入淡出→画面②→片尾卡，全程 BGM+字幕）。
跑 `analyze` 后再 `replicate`，可对照 `specs/ref_demo_report.md` 验证拆解与复刻是否还原。
