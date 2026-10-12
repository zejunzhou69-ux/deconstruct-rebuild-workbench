# -*- coding: utf-8 -*-
# 本文件为 dr-workbench 新增代码（MIT），基于 jianying-editor 技能运行时二次开发，详见 NOTICE.md
"""
对标分析引擎 (Reference Analyzer) v2
====================================
投喂一个「参考视频」，自动拆解它的剪辑手法，输出三份产物：
  1) <name>_spec.json       —— 机器可读的结构化时间轴（复刻引擎直接消费）
  2) <name>_report.md       —— 人话版「几分几秒用了什么」
  3) <name>_storyboard.md   —— 拆解后的「拍摄建议/分镜表」：告诉你拍哪些素材、怎么拍才好复刻

分析方法（全部基于开源工具，无需逆向）：
  - 镜头/转场：OpenCV 抽帧 + HSV 直方图相关性 + 边缘密度，
               区分 硬切 / 叠化 / 淡入淡出(过黑) / 黑场 / 字卡 / 画面
  - 字幕：Tesseract OCR（chi_sim）在**全分辨率帧**上识别文字行，
         给出位置(上/中/下) / 字号 / 颜色 / 出现与消失时间
  - 音频：ffmpeg silencedetect 找有声段，再对每段解码算 ZCR 粗分 人声/BGM
  - 滤镜痕迹：饱和度 / 亮度 / 电影黑边等可量化特征

用法：
  python reference_analyzer.py <参考视频.mp4> [--out specs]
"""
import os, sys, json, subprocess
import numpy as np
import cv2

# ---------------- 可调参数 ----------------
SAMPLE_FPS      = 5        # 分析抽帧率
HIST_CUT        = 0.55     # 硬切阈值
HIST_TRANS_LO   = 0.12     # 转场渐变下限
BRIGHT_BLACK    = 16       # 黑场亮度阈值
SAT_DESAT       = 20       # 灰度饱和度阈值
MOTION_CARD     = 0.05     # 动态低于此视为近静止
EDGE_CARD       = 0.030    # 边缘密度低于此 + 近静止 → 字卡（实拍画面边缘密度通常更高）
EDGE_CUT        = 0.025    # 边缘密度突变超过此值也判为切点
from paths import FFMPEG, TESSERACT
WORKDIR         = os.path.dirname(os.path.abspath(__file__))

TYPE_ZH   = {"footage": "画面", "card": "标题/字卡", "black": "黑场"}
TRANS_ZH  = {"hard_cut": "硬切", "dissolve": "叠化", "fade_black": "淡入淡出(过黑)", "none": "无"}
AUDIO_ZH  = {"speech": "人声旁白", "music": "BGM", "silence": "静音", "unknown": "音频"}


def _probe(path):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n   = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    return fps, n, w, h


def _sample_frames(path, sample_fps):
    """抽帧，返回 [(t, hist, bright, sat, small_bgr, edge_density), ...]"""
    cap = cv2.VideoCapture(path)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if src_fps < 1:
        src_fps = 25.0
    interval = max(1, round(src_fps / sample_fps))
    out = []
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    max_iter = min(n * 2 + 10, 6000) if n else 6000
    idx = 0
    while idx < max_iter:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % interval == 0:
            t = idx / src_fps
            small = cv2.resize(frame, (320, 180))
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            bright = float(hsv[:, :, 2].mean())
            sat = float(hsv[:, :, 1].mean())
            edge = float(cv2.Canny(small, 80, 160).mean()) / 255.0
            out.append((t, hist, bright, sat, small.copy(), edge))
        idx += 1
    cap.release()
    return out


def _detect_shots(samples):
    """稳定段分割：稳定区域=镜头，段间渐变=转场（不单独成镜头）。
    输出边界连续（转场帧并入相邻镜头），每个镜头带 transition_in/out。"""
    n = len(samples)
    if n < 2:
        return [{"start": 0.0, "end": samples[0][0] if n else 0.0, "duration": 0.0,
                 "type": "footage", "transition_in": "none", "transition_out": "none"}]
    diffs = [0.0]
    for i in range(1, n):
        c = cv2.compareHist(samples[i][1], samples[i - 1][1], cv2.HISTCMP_CORREL)
        diffs.append(max(0.0, 1.0 - c))
    brights = [s[2] for s in samples]
    edges   = [s[5] for s in samples]

    # 切点信号：直方图突变 OR 边缘密度突变（后者可区分「大色块画面」与「近黑字卡」）
    IN = 0.30
    cut = [False] * n
    for i in range(1, n):
        if diffs[i] >= IN:
            cut[i] = True
        elif abs(edges[i] - edges[i - 1]) >= EDGE_CUT:
            cut[i] = True

    runs = []
    i = 0
    while i < n:
        if i > 0 and cut[i]:
            i += 1
            continue
        j = i
        while j < n and (j == 0 or not cut[j]):
            j += 1
        if j - i >= 1:
            runs.append([i, j])
        i = j if j > i else i + 1
    if not runs:
        runs = [[0, n]]
    # 合并过短段（<0.4s）到前一段
    merged = []
    for s, e in runs:
        if merged and (samples[min(e, n - 1)][0] - samples[merged[-1][0]][0]) < 0.4:
            merged[-1][1] = e
        else:
            merged.append([s, e])

    # 连续边界：每个间隙取中点切开，转场帧并入相邻镜头
    bounds = [0]
    for k in range(len(merged) - 1):
        ga, gb = merged[k][1], merged[k + 1][0]
        bounds.append((ga + gb) // 2 if gb > ga else ga)
    bounds.append(n)

    def gap_type(a_idx, b_idx):
        """两个稳定段之间的转场类型"""
        hi = min(b_idx, n)
        if any(brights[x] < BRIGHT_BLACK for x in range(a_idx, hi)):
            return "fade_black"          # 经过黑场 → 淡入淡出
        seg = diffs[a_idx:hi] if hi > a_idx else []
        if seg and max(seg) > HIST_CUT:
            return "hard_cut"
        gradual = [d for d in seg if d > HIST_TRANS_LO]
        if len(gradual) >= 2:            # 连续多帧渐变才算叠化（单帧渐变多为硬切）
            return "dissolve"
        return "hard_cut"

    shots = []
    for k in range(len(bounds) - 1):
        s, e = bounds[k], bounds[k + 1]
        if e <= s:
            continue
        t0 = samples[s][0]
        t1 = samples[min(e, n - 1)][0]
        seg_bright = [brights[x] for x in range(s, min(e, n))]
        seg_edge   = [edges[x]   for x in range(s, min(e, n))]
        seg_diff   = [diffs[x]   for x in range(s + 1, min(e, n))] if e - s > 1 else [0]
        avg_motion = float(np.mean(seg_diff)) if seg_diff else 0.0
        avg_edge   = float(np.mean(seg_edge)) if seg_edge else 0.0
        if np.mean(seg_bright) < BRIGHT_BLACK:
            typ = "black"
        elif avg_motion < MOTION_CARD and avg_edge < EDGE_CARD:
            typ = "card"                  # 近静止 + 低细节 → 标题/字卡
        else:
            typ = "footage"
        tr_in = "none" if k == 0 else gap_type(merged[k - 1][1], merged[k][0])
        shots.append({
            "start": round(t0, 2), "end": round(t1, 2),
            "duration": round(t1 - t0, 2),
            "type": typ, "transition_in": tr_in, "transition_out": "none",
        })
    for k in range(len(shots) - 1):
        shots[k]["transition_out"] = gap_type(merged[k][1], merged[k + 1][0])
    return shots


def _ocr_lines(data, band_h, frame_h, y_offset=0.0):
    """把 image_to_data 的词框聚成文本行（行内按 x 排序，避免中文字序被打乱）。
    返回 [(text, y_center_norm, h_norm), ...]"""
    confs = data.get("conf", [])
    words = []
    for i, txt in enumerate(data.get("text", [])):
        txt = (txt or "").strip()
        try:
            conf = int(confs[i]) if i < len(confs) else 0
        except Exception:
            conf = 0
        if len(txt) >= 1 and conf >= 45:
            words.append((data["top"][i], data["height"][i], data["left"][i], txt))
    if not words:
        return []
    words.sort(key=lambda w: (w[0], w[2]))
    mh = float(np.median([w[1] for w in words])) or 10.0
    # 行聚类：与当前行垂直范围重叠（容差 0.3*中位字高）即归入同行
    lines = []
    for w in words:
        placed = False
        for ln in lines:
            if w[0] <= ln["bot"] + 0.3 * mh and w[0] + w[1] >= ln["top"] - 0.3 * mh:
                ln["words"].append(w)
                ln["top"] = min(ln["top"], w[0])
                ln["bot"] = max(ln["bot"], w[0] + w[1])
                placed = True
                break
        if not placed:
            lines.append({"words": [w], "top": w[0], "bot": w[0] + w[1]})
    out = []
    for ln in sorted(lines, key=lambda l: l["top"]):
        ws = sorted(ln["words"], key=lambda w: w[2])     # 行内按左边界排序 → 正确字序
        text = "".join(w[3] for w in ws)
        hgt = max(1, ln["bot"] - ln["top"])
        out.append((text, (y_offset + ln["top"] + hgt / 2.0) / frame_h, hgt / frame_h))
    return out


def _prep_bw(frame, thr=185, k=3):
    """HSV-V 阈值二值化 + 开运算去斑点：亮文字→白，其余→黑"""
    v = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 2]
    _, bw = cv2.threshold(v, thr, 255, cv2.THRESH_BINARY)
    if k >= 2:
        ker = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, ker)
    return bw


# OCR 条带（起点, 终点）—— 覆盖 顶部标题 / 居中标题 / 底部字幕
OCR_BANDS = [(0.0, 0.38), (0.30, 0.70), (0.60, 1.0)]
OCR_THRESHOLDS = (185, 212)   # 双阈值：兼顾普通背景与高亮彩色背景


def _detect_subtitles(path, times, tesseract=TESSERACT, max_samples=30):
    """多通道 OCR（上/中/下条带 × 双阈值），按文本相似度聚类，多帧投票过滤噪声。
    返回字幕块列表。"""
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = tesseract
    except Exception:
        return []
    times = sorted(set(round(t, 3) for t in times))
    if len(times) > max_samples:               # 样本数上限，保证速度
        step = len(times) / float(max_samples)
        times = [times[int(i * step)] for i in range(max_samples)]

    hits = []          # (t, text, y_norm, h_norm)
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    for t in times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(t * fps))))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        H, W = frame.shape[:2]
        for thr in OCR_THRESHOLDS:
            bw = _prep_bw(frame, thr)
            for b0, b1 in OCR_BANDS:
                y0, y1 = int(H * b0), int(H * b1)
                img = bw[y0:y1, :]
                try:
                    data = pytesseract.image_to_data(img, lang="chi_sim+eng",
                                                     output_type=pytesseract.Output.DICT,
                                                     config="--psm 6", timeout=25)
                except Exception:
                    continue
                for text, yc, hh in _ocr_lines(data, img.shape[0], H, float(y0)):
                    hits.append((t, text, yc, hh))
    cap.release()
    if not hits:
        return []
    # 同一 (时刻, 文本) 去重
    dedup, seen = [], set()
    for h in hits:
        key = (h[0], h[1])
        if key not in seen:
            seen.add(key)
            dedup.append(h)
    hits = dedup
    # 按「文本相似 + 时间接近」聚类成多组（解决交替文本无法自合并的问题）
    groups = []
    for t, text, yc, hh in hits:
        best = None
        for g in groups:
            if abs(g["last_t"] - t) <= 2.0 and _sim(g["text"], text) >= 0.45:
                best = g
                break
        if best is not None:
            best["hits"].append((t, text, yc, hh))
            best["last_t"] = t
            if len(text) > len(best["text"]):
                best["text"] = text
        else:
            groups.append({"text": text, "hits": [(t, text, yc, hh)], "last_t": t})
    blocks = [_finalize_block(g) for g in groups]
    # 噪声过滤：长度 >= 2、持续 >= 0.4s、符号占比不过半
    out = []
    for b in blocks:
        if len(b["text"]) < 2 or (b["end"] - b["start"]) < 0.4:
            continue
        if _symbol_ratio(b["text"]) > 0.5:
            continue
        out.append(b)
    # 同一字幕的不同部分识别：时间范围高度重叠 + 垂直位置接近 → 合并，取更优文本
    merged = []
    for b in sorted(out, key=lambda x: -(x["end"] - x["start"])):
        placed = False
        for m in merged:
            ov = min(b["end"], m["end"]) - max(b["start"], m["start"])
            shorter = min(b["end"] - b["start"], m["end"] - m["start"])
            if shorter > 0 and ov > 0.5 * shorter and abs(b["_y"] - m["_y"]) < 0.12:
                if _text_score(b["text"]) > _text_score(m["text"]):
                    m["text"] = b["text"]
                    m["size"] = b["size"]
                m["start"] = round(min(m["start"], b["start"]), 2)
                m["end"] = round(max(m["end"], b["end"]), 2)
                placed = True
                break
        if not placed:
            merged.append(b)
    for b in merged:
        b.pop("_y", None)
    merged.sort(key=lambda b: b["start"])
    return merged


def _text_score(s):
    """文本质量分：长而少符号者优"""
    return len(s) - 3 * round(_symbol_ratio(s) * len(s))


def _symbol_ratio(s):
    """非中文、非字母数字的字符占比（用于过滤 OCR 噪声）"""
    if not s:
        return 1.0
    bad = sum(1 for ch in s
              if not ('\u4e00' <= ch <= '\u9fff' or ch.isalnum()))
    return bad / len(s)


def _sim(a, b):
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _finalize_block(g):
    """把一组命中收敛成一个字幕块（位置取均值，文本取质量最优变体）"""
    ys = [h[2] for h in g["hits"]]
    hs = [h[3] for h in g["hits"]]
    ts = [h[0] for h in g["hits"]]
    y = float(np.mean(ys))
    if y > 0.72:
        pos = "bottom"
    elif y > 0.35:
        pos = "middle"
    else:
        pos = "top"
    # 文本质量加权投票：长而少符号的变体得分高
    variants = [h[1] for h in g["hits"]]
    text = max(variants,
               key=lambda s: (len(s) - 3 * round(_symbol_ratio(s) * len(s)), s))
    return {
        "start": round(min(ts), 2), "end": round(max(ts), 2),
        "text": text, "position": pos,
        "size": _size_label(float(np.mean(hs))),
        "color": "white", "outline": "black", "_y": y,
    }


def _size_label(h_norm):
    if h_norm < 0.045:
        return "small"
    if h_norm < 0.085:
        return "medium"
    return "large"


def _classify_segment(path, a, b):
    """解码有声段的一小段，用 ZCR 粗分 人声(高ZCR) / 音乐(低ZCR)"""
    dur = max(0.3, min(b - a, 20.0))
    try:
        r = subprocess.run([FFMPEG, "-ss", f"{a:.2f}", "-t", f"{dur:.2f}", "-i", path,
                            "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                           capture_output=True, timeout=60)
        raw = r.stdout
    except Exception:
        return "music"
    if len(raw) < 200:
        return "music"
    sig = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if len(sig) < 200:
        return "music"
    s = np.sign(sig)
    s[s == 0] = 1
    zcr = float(np.mean(np.abs(np.diff(s))))
    rms = float(np.sqrt(np.mean(sig ** 2)))
    if rms < 0.004:
        return "silence"
    return "speech" if zcr > 0.06 else "music"


def _analyze_audio(path, duration):
    """silencedetect 找静音 → 反推有声段 → 每段 ZCR 分人声/BGM"""
    try:
        r = subprocess.run([FFMPEG, "-i", path, "-af", "silencedetect=n=-35dB:d=0.4",
                            "-f", "null", "-"], capture_output=True, text=True, timeout=180)
        out = r.stderr
    except Exception:
        return [{"start": 0.0, "end": round(duration, 2), "type": "unknown"}]
    silences, cur = [], {}
    for line in out.splitlines():
        if "silence_start" in line:
            try:
                cur["start"] = float(line.split("silence_start:")[-1].strip())
            except Exception:
                pass
        elif "silence_end" in line:
            try:
                cur["end"] = float(line.split("silence_end:")[-1].split()[0])
                silences.append((cur.get("start", 0.0), cur["end"]))
                cur = {}
            except Exception:
                pass
    segs, prev = [], 0.0
    for s, e in silences:
        if s > prev + 0.15:
            segs.append((prev, s))
        prev = max(prev, e)
    if duration - prev > 0.15:
        segs.append((prev, duration))
    out_list = []
    for a, b in segs:
        out_list.append({"start": round(a, 2), "end": round(b, 2),
                         "type": _classify_segment(path, a, b)})
    for s, e in silences:
        out_list.append({"start": round(s, 2), "end": round(e, 2), "type": "silence"})
    out_list.sort(key=lambda x: x["start"])
    merged = []
    for seg in out_list:
        if merged and merged[-1]["type"] == seg["type"] and seg["start"] - merged[-1]["end"] < 0.3:
            merged[-1]["end"] = seg["end"]
        else:
            merged.append(dict(seg))
    merged = [m for m in merged if (m["end"] - m["start"]) >= 0.3]
    return merged or [{"start": 0.0, "end": round(duration, 2), "type": "unknown"}]


def _detect_filters(samples):
    flags = []
    sats = [s[3] for s in samples]
    brights = [s[2] for s in samples]
    if np.mean(sats) < SAT_DESAT:
        flags.append("灰度 / 黑白")
    if np.mean(brights) < 60:
        flags.append("低亮度 / 暗调")
    elif np.mean(brights) > 180:
        flags.append("高亮 / 清新")
    edges_black = 0
    sampled = samples[::3]
    for s in sampled:
        small = s[4]
        if small[:6, :].mean() < 12 and small[-6:, :].mean() < 12:
            edges_black += 1
    if sampled and edges_black > len(sampled) * 0.4:
        flags.append("电影黑边 (letterbox)")
    if not flags:
        flags.append("未识别（需人工确认）")
    return flags


def analyze(path, out_dir="specs"):
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    fps, n, w, h = _probe(path)
    duration = n / fps if fps else 0
    print(f"[1/5] 抽帧分析: {stem}  ({w}x{h}, {fps:.1f}fps, ~{duration:.1f}s)", flush=True)
    samples = _sample_frames(path, SAMPLE_FPS)
    print(f"      采样 {len(samples)} 帧", flush=True)

    print("[2/5] 镜头 / 转场检测 ...", flush=True)
    shots = _detect_shots(samples)

    print("[3/5] 字幕 OCR (Tesseract, 全分辨率帧) ...", flush=True)
    stride = max(1, round(0.8 * SAMPLE_FPS))
    ocr_times = [s[0] for s in samples[::stride]]
    subs = _detect_subtitles(path, ocr_times)
    print(f"      识别到 {len(subs)} 个字幕块", flush=True)

    print("[4/5] 音频分段 (语音/BGM/静音) ...", flush=True)
    audio = _analyze_audio(path, duration)

    print("[5/5] 滤镜痕迹 / 风格汇总 ...", flush=True)
    filters = _detect_filters(samples)

    # 风格画像
    shot_lens = [s["duration"] for s in shots if s["type"] == "footage"]
    avg_len = float(np.mean(shot_lens)) if shot_lens else (duration / max(1, len(shots)))
    pace = "fast" if avg_len < 1.5 else ("slow" if avg_len > 4 else "medium")
    sub_style = None
    if subs:
        # 取「底部 + 持续最久」的字幕块作为风格样本（最接近常规字幕样式）
        bottom_blocks = [b for b in subs if b["position"] == "bottom"]
        pool = bottom_blocks if bottom_blocks else subs
        sample = max(pool, key=lambda b: b["end"] - b["start"])
        sub_style = {"present": True, "position": sample["position"],
                     "size": sample.get("size", "medium"),
                     "color": sample.get("color", "white"),
                     "outline": sample.get("outline", "black")}
    music_types = [a["type"] for a in audio if a["type"] == "music"]
    speech_types = [a["type"] for a in audio if a["type"] == "speech"]
    style = {
        "orientation": "vertical" if h > w else "horizontal",
        "resolution": {"w": w, "h": h},
        "pacing": pace,
        "avg_shot_len": round(avg_len, 2),
        "color_tone": _tone(samples),
        "has_title_card": shots[0]["type"] in ("card", "black") if shots else False,
        "has_end_card": shots[-1]["type"] in ("card", "black") if shots else False,
        "subtitle_style": sub_style,
        "music": {"present": bool(music_types), "segments": len(music_types)},
        "narration": {"present": bool(speech_types), "segments": len(speech_types)},
        "filters": filters,
    }

    timeline = _build_timeline(shots, subs, audio)

    spec = {
        "source": os.path.basename(path),
        "duration": round(duration, 2),
        "fps": round(fps, 2),
        "style_profile": style,
        "shots": shots,
        "subtitles": subs,
        "audio_segments": audio,
        "timeline": timeline,
        "_meta": "generated by 剪辑工作台·对标分析引擎 v2",
    }

    os.makedirs(out_dir, exist_ok=True)
    spec_path = os.path.join(out_dir, f"{stem}_spec.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    rep_path = os.path.join(out_dir, f"{stem}_report.md")
    _write_report(rep_path, spec)
    sb_path = os.path.join(out_dir, f"{stem}_storyboard.md")
    _write_storyboard(sb_path, spec)
    print(f"\n✅ 完成。输出：\n   {spec_path}\n   {rep_path}\n   {sb_path}", flush=True)
    return spec, spec_path, rep_path


def _tone(samples):
    m = np.mean([s[3] for s in samples])
    return "desaturated" if m < SAT_DESAT else "normal"


def _build_timeline(shots, subs, audio):
    tl = []
    for k, s in enumerate(shots):
        note = TYPE_ZH[s["type"]]
        if s["transition_in"] not in ("none", None):
            note += f" · 入:{TRANS_ZH.get(s['transition_in'], s['transition_in'])}"
        if s["transition_out"] not in ("none", None):
            note += f" · 出:{TRANS_ZH.get(s['transition_out'], s['transition_out'])}"
        tl.append({"t": s["start"], "what": f"镜头{k+1} [{note}] {s['start']}s→{s['end']}s"})
    for a in audio:
        if a["type"] == "silence":
            continue
        tl.append({"t": a["start"], "what": f"音频:{AUDIO_ZH.get(a['type'], a['type'])} {a['start']}s→{a['end']}s"})
    for sub in subs:
        tl.append({"t": sub["start"], "what": f"字幕:「{sub['text']}」 {sub['start']}s→{sub['end']}s ({sub.get('size')},{sub.get('color')})"})
    tl.sort(key=lambda x: x["t"])
    return tl


def _write_storyboard(path, spec):
    """拆解后的「拍摄建议 / 分镜表」——告诉用户该拍哪些素材、怎么拍才好复刻。"""
    s = spec["style_profile"]
    shots = spec["shots"]
    subs = spec["subtitles"]
    footage = [x for x in shots if x["type"] == "footage"]
    total_footage = sum(x["duration"] for x in footage)
    orient = "竖屏 9:16" if s["orientation"] == "vertical" else "横屏 16:9"
    has_sub = bool(s.get("subtitle_style"))
    n_narr = max(len(subs), len(footage)) if footage else len(subs)
    music = "有（建议选与参考一致的轻快/舒缓纯音乐，避免人声抢戏）" if s["music"]["present"] else "无"
    narr_present = s.get("narration", {}).get("present", False)

    L = []
    L.append(f"# 复刻拍摄建议（分镜表）：{spec['source']}\n")
    L.append("> 这是「对标拆解」之后给你的**实拍指南**：照这张表准备素材，再用 `replicate` 就能高还原度复刻。\n")
    L.append("## 一、你需要准备什么")
    L.append(f"- **画幅**：{orient}（分辨率 {s['resolution']['w']}x{s['resolution']['h']}）")
    L.append(f"- **画面素材**：{len(footage)} 段，合计约 **{total_footage:.0f}s**（见下表各镜时长）")
    L.append(f"- **片头/片尾卡**：{'需要（用纯色背景+文字即可，无需实拍）' if (s['has_title_card'] or s['has_end_card']) else '不需要'}")
    if has_sub:
        L.append(f"- **旁白/字幕文案**：需要约 {n_narr} 句（每镜一句，复刻的是风格不是原话）")
    if narr_present:
        L.append("- **配音**：参考片有人声旁白；复刻时可用 AI 配音自动生成（无需自己录音）")
    L.append(f"- **背景音乐**：{music}")
    L.append("")
    L.append("## 二、分镜拍摄表（照拍即可）")
    L.append("| 镜号 | 类型 | 建议时长 | 转场(出) | 旁白/字幕 | 拍什么（建议） |")
    L.append("|------|------|----------|----------|-----------|----------------|")
    for i, sh in enumerate(shots, 1):
        tr = TRANS_ZH.get(sh["transition_out"], "无") if sh["transition_out"] not in ("none", None) else "无"
        typ = TYPE_ZH[sh["type"]]
        if sh["type"] == "card":
            role = "片头卡" if i == 1 else ("片尾卡" if i == len(shots) else "字卡")
            advice = f"{role}：纯色/渐变背景 + 标题文字（可用工具生成，不必实拍）"
            narr = "—"
        elif sh["type"] == "black":
            advice = "可留黑场过渡，或实拍一段暗场"
            narr = "—"
        else:
            overlap = [sub for sub in subs if not (sub["end"] < sh["start"] or sub["start"] > sh["end"])]
            if has_sub and overlap:
                ref_text = overlap[0]["text"]
                narr = f"要一句旁白+字幕（参考此镜原话：「{ref_text}」）"
            else:
                narr = "纯画面（配乐即可）"
            advice = ("拍主体清晰、光线稳定的画面；避免黑场与大幅抖动；"
                      "时长贴近建议值，内容围绕你的主题自由发挥")
        L.append(f"| {i} | {typ} | {sh['duration']}s | {tr} | {narr} | {advice} |")
    L.append("")
    L.append("## 三、素材清单（勾选）")
    L.append(f"- [ ] {len(footage)} 段{orient}画面，每段按上表时长（建议手机原画质，每段多拍 2-3 条备选）")
    if s["has_title_card"]:
        L.append("- [ ] 片头标题文字（如：___）")
    if s["has_end_card"]:
        L.append("- [ ] 片尾标语文字（如：___）")
    if has_sub:
        L.append(f"- [ ] 旁白文案 {n_narr} 句（写进 .txt 或直接在命令里给，AI 会自动配音+字幕）")
    if s["music"]["present"]:
        L.append("- [ ] 一段轻柔 BGM（或用工具自动生成）")
    L.append("")
    L.append("## 四、拍摄小贴士")
    L.append("- 手机拍摄时锁好横竖屏，与参考画幅一致，避免复刻时裁切变形")
    L.append("- 每个镜头拍长一点（比建议时长多 2-3 秒），后期好裁剪")
    L.append("- 光线稳定、手别抖（可依靠物体或开防抖）；避免逆光黑脸")
    L.append("- 参考片是「快节奏」就多拍短镜头，是「慢节奏」就拍长镜头+缓慢运镜")
    L.append("")
    L.append("## 五、复刻命令（素材齐了直接跑）")
    L.append("```bat")
    L.append(f"python workbench.py replicate \"specs\\{os.path.splitext(spec['source'])[0]}_spec.json\" \"你的素材文件夹\" ^")
    if s["has_title_card"] or s["has_end_card"]:
        L.append(f"    --title \"你的标题\" ^")
    if has_sub:
        L.append(f"    --narration \"这里写{n_narr}句旁白文案，自动拆句配音加字幕\" ^")
    L.append(f"    --out 成品")
    L.append("```")
    L.append("\n> 提示：转场/节奏/字幕样式/配乐摆放会自动按对标还原；具体滤镜调色为同风格近似。")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def _write_report(path, spec):
    s = spec["style_profile"]
    lines = []
    lines.append(f"# 对标分析报告：{spec['source']}\n")
    lines.append("> 自动拆解自参考视频，供「复刻」阶段消费。\n")
    lines.append("## 一、整体画像")
    lines.append(f"- 时长：**{spec['duration']}s**，分辨率 **{s['resolution']['w']}x{s['resolution']['h']}**（{s['orientation']}）")
    lines.append(f"- 剪辑节奏：**{ {'fast':'快剪','medium':'中速','slow':'慢节奏'}[s['pacing']] }**（平均镜头 {s['avg_shot_len']}s）")
    lines.append(f"- 色调：**{s['color_tone']}**")
    lines.append(f"- 片头卡：{'有' if s['has_title_card'] else '无'} ｜ 片尾卡：{'有' if s['has_end_card'] else '无'}")
    if s["subtitle_style"]:
        ss = s["subtitle_style"]
        lines.append(f"- 字幕：有（位置 {ss['position']}｜字号 {ss['size']}｜{ss['color']}字{ss['outline']}描边）")
    else:
        lines.append("- 字幕：未检测到")
    lines.append(f"- 背景音乐：{'有' if s['music']['present'] else '无'}（{s['music']['segments']} 段）")
    if s.get("narration", {}).get("present"):
        lines.append(f"- 人声旁白：有（{s['narration']['segments']} 段）")
    lines.append(f"- 滤镜痕迹：{', '.join(s['filters'])}")
    lines.append("\n## 二、时间轴拆解（几分几秒用了什么）")
    for item in spec["timeline"]:
        ts = _fmt_ts(item["t"])
        lines.append(f"- **{ts}**　{item['what']}")
    lines.append("\n## 三、镜头清单")
    for k, sh in enumerate(spec["shots"]):
        lines.append(f"{k+1}. {sh['start']}s→{sh['end']}s（{sh['duration']}s）[{TYPE_ZH[sh['type']]}] "
                     f"入:{TRANS_ZH.get(sh['transition_in'], sh['transition_in'])} "
                     f"出:{TRANS_ZH.get(sh['transition_out'], sh['transition_out'])}")
    if spec["subtitles"]:
        lines.append("\n## 四、字幕清单")
        for sub in spec["subtitles"]:
            lines.append(f"- {_fmt_ts(sub['start'])}→{_fmt_ts(sub['end'])}：「{sub['text']}」")
    lines.append("\n## 五、复刻建议")
    lines.append("- 拆解后的**拍摄建议 / 分镜表**见同目录 `<名>_storyboard.md`，照表准备素材即可高还原复刻。")
    lines.append("- 用 `replicate` 投喂你自己的素材，本工具会按上面的镜头数/转场/节奏/字幕风格/BGM 摆放自动复刻。")
    lines.append("- 字幕内容需要你提供旁白文案（复刻的是「风格」不是「原话」）。")
    lines.append("- 滤镜类（如特定 LUT）无法 100% 逆向，复刻为同风格近似处理。")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _fmt_ts(sec):
    sec = int(round(sec))
    m, s = divmod(sec, 60)
    return f"{m:d}:{s:02d}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python reference_analyzer.py <参考视频.mp4> [--out specs]")
        sys.exit(1)
    out = "specs"
    args = sys.argv[1:]
    if "--out" in args:
        out = args[args.index("--out") + 1]
        args.remove("--out"); args.remove(out)
    analyze(args[0], out)
