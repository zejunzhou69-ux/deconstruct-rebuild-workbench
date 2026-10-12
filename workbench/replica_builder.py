# -*- coding: utf-8 -*-
# 本文件为 deconstruct-rebuild-workbench 新增代码（MIT），基于 jianying-editor 技能运行时二次开发，详见 NOTICE.md
"""
复刻引擎 (Replica Builder) v2
=============================
读取对标分析产出的 <name>_spec.json，再投喂你自己的素材，
按参考视频的「镜头数 / 转场类型 / 节奏 / 字幕风格 / BGM 摆放」自动复刻。

输出：
  - 成品/<name>_复刻.mp4      （ffmpeg 渲染，可双击播放）
  - 剪映草稿「<title>」        （best-effort，可在剪映里继续精修）

用法：
  python replica_builder.py <spec.json> <素材文件夹> \
      [--title "标题"] [--narration script.txt|"文本"] [--bgm file.mp3] [--out 成品]
"""
import os, sys, json, subprocess, tempfile, asyncio, shutil, glob, re
import numpy as np

from paths import FFMPEG as FF, FFPROBE as FP, SKILL
WORKDIR = os.path.dirname(os.path.abspath(__file__))
FADE_DUR = 0.4          # 淡入淡出(过黑) 单侧时长
DISSOLVE_DUR = 0.5      # 叠化时长


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print("  ⚠ ffmpeg 警告/错误:\n", (r.stderr or "")[-600:])
    return r


def _probe_dur(path):
    r = subprocess.run([FP, "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 3.0


def _tts(text, out_path, voice="zh-CN-XiaoxiaoNeural", retries=4):
    """edge-tts 合成（带重试，规避服务端限流 429）"""
    import edge_tts, time
    async def go():
        comm = edge_tts.Communicate(text, voice)
        await comm.save(out_path)
    last = None
    for attempt in range(retries):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(go())
            return
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
        finally:
            try:
                loop.close()
            except Exception:
                pass
    raise last


def _gen_music(out_wav, length, sr=44100):
    """简短 lo-fi 循环（无版权），用于复刻 BGM 风格"""
    t = np.linspace(0, length, int(sr * length), endpoint=False)
    prog = [(220.0, 261.63), (220.0, 329.63), (174.61, 261.63), (196.0, 246.94)]
    beat = 2.0
    music = np.zeros_like(t)
    for i, (f1, f2) in enumerate(prog):
        seg = np.logical_and(t >= i * beat, t < (i + 1) * beat)
        tt = t[seg] - i * beat
        env = np.exp(-3 * tt) * (1 - np.exp(-30 * tt))
        music[seg] = (np.sin(2 * np.pi * f1 * tt) + 0.5 * np.sin(2 * np.pi * f2 * tt)) * env * 0.18
    music += np.random.randn(len(t)).astype(np.float32) * 0.004
    music = np.clip(music, -1, 1)
    import wave
    wf = wave.open(out_wav, "wb")
    wf.setnchannels(2); wf.setsampwidth(2); wf.setframerate(sr)
    wf.writeframes((music * 32767).astype(np.int16).tobytes())
    wf.close()


def _make_card(text, w, h, out_png, subtitle=None, dark=True):
    from PIL import Image, ImageDraw, ImageFont
    bg = (16, 18, 28) if dark else (245, 246, 250)
    fg = (255, 255, 255) if dark else (20, 22, 30)
    sub_fg = (180, 200, 255) if dark else (90, 100, 130)
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    try:
        f1 = ImageFont.truetype(r"C:\Windows\Fonts\simhei.ttf", int(h * 0.06))
        f2 = ImageFont.truetype(r"C:\Windows\Fonts\simhei.ttf", int(h * 0.028))
    except Exception:
        f1 = f2 = ImageFont.load_default()
    d.text((w // 2, int(h * 0.42)), text, fill=fg, font=f1, anchor="mm")
    if subtitle:
        d.text((w // 2, int(h * 0.52)), subtitle, fill=sub_fg, font=f2, anchor="mm")
    img.save(out_png)


def _split_sentences(text):
    parts = re.split(r"(?<=[。！？!?；;])", text)
    return [p.strip() for p in parts if p.strip()]


def _build_segment(src, out, w, h, dur, fade_in=0.0, fade_out=0.0):
    """把单个素材缩放+裁补+截时长+淡入淡出，编码成统一规格片段"""
    vf = [f"scale={w}:{h}:force_original_aspect_ratio=decrease",
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
          "fps=30", "format=yuv420p"]
    if fade_in > 0:
        vf.append(f"fade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        vf.append(f"fade=t=out:st={max(0.0, dur - fade_out):.2f}:d={fade_out}")
    _run([FF, "-y", "-i", src, "-t", str(dur), "-vf", ",".join(vf),
          "-c:v", "libx264", "-preset", "veryfast", "-an", out])


def _img_segment(img, out, w, h, dur, fade_in=0.0, fade_out=0.0):
    vf = [f"scale={w}:{h}:force_original_aspect_ratio=decrease",
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
          "fps=30", "format=yuv420p"]
    if fade_in > 0:
        vf.append(f"fade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        vf.append(f"fade=t=out:st={max(0.0, dur - fade_out):.2f}:d={fade_out}")
    _run([FF, "-y", "-loop", "1", "-t", str(dur), "-i", img, "-vf", ",".join(vf),
          "-c:v", "libx264", "-preset", "veryfast", "-an", out])


def build(spec_path, media_dir, title="我的复刻作品", narration=None,
          bgm=None, out_dir="成品", end_text="感谢观看"):
    spec = json.load(open(spec_path, encoding="utf-8"))
    sp = spec["style_profile"]
    W, H = sp["resolution"]["w"], sp["resolution"]["h"]
    stem = os.path.splitext(os.path.basename(spec_path))[0].replace("_spec", "")

    all_shots = spec["shots"]
    footage_shots = [s for s in all_shots if s["type"] == "footage"]
    ref_durs = [s["duration"] for s in footage_shots] or [3.0]
    # 相邻画面镜头之间的转场（用 transition_in 表达）
    ref_trans = [s.get("transition_in", "hard_cut") for s in footage_shots[1:]]
    title_card = all_shots[0] if (all_shots and sp.get("has_title_card")) else None
    end_card = all_shots[-1] if (all_shots and sp.get("has_end_card")) else None

    media = []
    for ext in ("*.mp4", "*.mov", "*.mkv", "*.webm"):
        media += sorted(glob.glob(os.path.join(media_dir, ext)))
    for ext in ("*.jpg", "*.jpeg", "*.png"):
        media += sorted(glob.glob(os.path.join(media_dir, ext)))
    if not media:
        raise SystemExit("素材文件夹里没有视频/图片")
    print(f"[复刻] 素材 {len(media)} 个；参考画面镜头 {len(ref_durs)} 个，"
          f"片头卡={'有' if title_card else '无'}，片尾卡={'有' if end_card else '无'}")

    tmp = tempfile.mkdtemp(prefix="replica_")
    segs = []          # (file, dur, trans_in)
    n = max(len(ref_durs), 1)
    for i in range(n):
        src = media[i % len(media)]
        dur = ref_durs[i % len(ref_durs)]
        trans_in = ref_trans[i - 1] if (i > 0 and i - 1 < len(ref_trans)) else "hard_cut"
        # 淡入淡出(过黑)：本段入/上一段出
        fade_in = FADE_DUR if trans_in == "fade_black" else 0.0
        next_trans = ref_trans[i] if i < len(ref_trans) else "hard_cut"
        fade_out = FADE_DUR if next_trans == "fade_black" else 0.0
        out = os.path.join(tmp, f"seg{i}.mp4")
        if src.lower().endswith((".jpg", ".jpeg", ".png")):
            _img_segment(src, out, W, H, dur, fade_in, fade_out)
        else:
            _build_segment(src, out, W, H, dur, fade_in, fade_out)
        segs.append((out, dur, trans_in))

    # 片头卡 / 片尾卡
    if title_card:
        tp = os.path.join(tmp, "title.png")
        _make_card(title, W, H, tp, subtitle="按对标参考复刻")
        tc = os.path.join(tmp, "title.mp4")
        _img_segment(tp, tc, W, H, max(title_card["duration"], 2.0))
        segs.insert(0, (tc, max(title_card["duration"], 2.0), "hard_cut"))
    if end_card:
        ep = os.path.join(tmp, "end.png")
        _make_card(end_text, W, H, ep, dark=True)
        ec = os.path.join(tmp, "end.mp4")
        _img_segment(ep, ec, W, H, max(end_card["duration"], 2.0))
        segs.append((ec, max(end_card["duration"], 2.0), "hard_cut"))

    # ---- 串联视频（dissolve 用 xfade，其余 concat）----
    inputs, fparts = [], []
    for f, _, _ in segs:
        inputs += ["-i", f]
    prev, off = "0:v", segs[0][1]
    for i in range(1, len(segs)):
        d_i, tr = segs[i][1], segs[i][2]
        if tr == "dissolve" and d_i > 0.8 and off > 0.8:
            fparts.append(f"[{prev}][{i}:v]xfade=transition=fade:"
                          f"duration={DISSOLVE_DUR}:offset={off:.3f}[v{i}]")
            off = off + d_i - DISSOLVE_DUR
        else:
            fparts.append(f"[{prev}][{i}:v]concat=n=2:v=1:a=0[v{i}]")
            off = off + d_i
        prev = f"v{i}"
    total = off
    video0 = os.path.join(tmp, "video0.mp4")
    _run([FF, "-y"] + inputs + ["-filter_complex", ";".join(fparts),
          "-map", f"[{prev}]", "-c:v", "libx264", "-preset", "veryfast",
          "-pix_fmt", "yuv420p", "-r", "30", video0])
    print(f"[复刻] 视频链完成：{total:.1f}s, {W}x{H}")

    # ---- 旁白：TTS → 实测时长 → 字幕时间轴与配音严格对齐 ----
    video_cur = video0
    if narration:
        sentences = _split_sentences(narration)
        if sentences:
            start_off = segs[0][1] if title_card else 0.0
            t = start_off
            placed = []          # (text, start, end, file)
            for k, s in enumerate(sentences):
                op = os.path.join(tmp, f"n{k}.ogg")
                try:
                    _tts(s, op)
                except Exception as e:
                    print("  ⚠ TTS 失败，跳过该句：", e)
                    continue
                d = _probe_dur(op)
                placed.append((s, t, t + d, op))
                t += d + 0.15
            if t > total + 0.5:
                print(f"  ⚠ 旁白总长 {t:.1f}s 超过视频 {total:.1f}s，尾部可能被截")
            srt = os.path.join(tmp, "subs.srt")
            _write_srt(srt, placed)
            # 烧录字幕（ffmpeg filter 里路径需转义 : 和 \）
            style = sp.get("subtitle_style") or {}
            video1 = os.path.join(tmp, "video1.mp4")
            st = _sub_style_str(style, W, H)
            srt_esc = srt.replace("\\", "\\\\").replace(":", "\\:")
            _run([FF, "-y", "-i", video_cur,
                  "-vf", f"subtitles=filename='{srt_esc}':force_style='{st}'",
                  "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                  "-r", "30", video1])
            video_cur = video1
            # 拼旁白轨
            narr_wav = os.path.join(tmp, "narr.wav")
            _concat_audio([p[3] for p in placed], [p[1] for p in placed],
                          narr_wav, total)
            audio_out = narr_wav
            if sp.get("music", {}).get("present"):
                bgm_wav = os.path.join(tmp, "bgm.wav")
                if bgm:
                    _to_wav(bgm, bgm_wav, total)
                else:
                    _gen_music(bgm_wav, total + 1)
                mixed = os.path.join(tmp, "mixed.wav")
                _mix(narr_wav, bgm_wav, mixed, total)
                audio_out = mixed
        else:
            audio_out = None
    else:
        # 无旁白：仅 BGM
        audio_out = None
        if sp.get("music", {}).get("present"):
            bgm_wav = os.path.join(tmp, "bgm.wav")
            if bgm:
                _to_wav(bgm, bgm_wav, total)
            else:
                _gen_music(bgm_wav, total + 1)
            audio_out = bgm_wav

    os.makedirs(out_dir, exist_ok=True)
    final = os.path.join(out_dir, f"{stem}_复刻.mp4")
    if audio_out:
        # apad 补静音 + 定时长，避免音频短于视频时被 -shortest 截断
        _run([FF, "-y", "-i", video_cur, "-i", audio_out,
              "-map", "0:v", "-map", "1:a", "-c:v", "copy",
              "-af", "apad", "-t", f"{total:.2f}",
              "-c:a", "aac", "-b:a", "192k", final])
    else:
        shutil.copy(video_cur, final)

    print(f"\n✅ 复刻完成：{final} （时长 {total:.1f}s, {W}x{H}）")
    # 剪映草稿（best-effort）
    try:
        _build_draft(spec, media_dir, title, narration)
    except Exception as e:
        print("  （剪映草稿生成跳过：", e, "）")
    shutil.rmtree(tmp, ignore_errors=True)
    return final


def _write_srt(path, placed):
    """按 TTS 实测时长写字幕（与配音严格对齐）"""
    def fmt(t):
        h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")
    with open(path, "w", encoding="utf-8") as f:
        for i, (text, a, b, _) in enumerate(placed, 1):
            f.write(f"{i}\n{fmt(a)} --> {fmt(b)}\n{text}\n\n")


def _sub_style_str(style, W, H):
    pos = style.get("position", "bottom")
    align = {"bottom": 2, "middle": 5, "top": 8}.get(pos, 2)
    marginv = int(H * 0.06) if pos == "bottom" else int(H * 0.04)
    size = {"small": int(H * 0.032), "medium": int(H * 0.045),
            "large": int(H * 0.06)}.get(style.get("size", "medium"), int(H * 0.045))
    color = "&H00FFFFFF" if style.get("color", "white") == "white" else "&H00000000"
    outline = "&H00000000" if style.get("outline", "black") == "black" else "&H00FFFFFF"
    return (f"PlayResX={W},PlayResY={H},FontName=SimHei,FontSize={size},"
            f"PrimaryColour={color},OutlineColour={outline},BorderStyle=1,Outline=3,"
            f"Alignment={align},MarginV={marginv}")


def _concat_audio(files, starts, out_wav, total, sr=44100):
    fc, inputs, amix = [], [], []
    for f in files:
        inputs += ["-i", f]
    for i, st in enumerate(starts):
        fc.append(f"[{i}:a]aformat=sample_rates={sr}:channel_layouts=stereo,"
                  f"adelay=delays={int(st * 1000)}:all=1[a{i}]")
        amix.append(f"[a{i}]")
    fc.append(f"{''.join(amix)}amix=inputs={len(files)}:duration=longest:normalize=0[aout]")
    fc.append(f"[aout]atrim=0:{total},asetpts=PTS-STARTPTS[aout2]")
    _run([FF, "-y"] + inputs + ["-filter_complex", ";".join(fc),
          "-map", "[aout2]", "-c:a", "pcm_s16le", out_wav])


def _to_wav(src, out_wav, total):
    _run([FF, "-y", "-i", src, "-t", str(total), "-ac", "1", "-ar", "44100",
          "-c:a", "pcm_s16le", out_wav])


def _mix(narr, bgm, out_wav, total):
    fc = (f"[0:a]volume=1.0[n];[1:a]volume=0.32[m];"
          f"[n][m]amix=inputs=2:duration=first:normalize=0[aout]")
    _run([FF, "-y", "-i", narr, "-i", bgm, "-filter_complex", fc,
          "-map", "[aout]", "-t", str(total), "-c:a", "pcm_s16le", out_wav])


def _build_draft(spec, media_dir, title, narration):
    """best-effort 生成剪映草稿（便于继续精修）"""
    sys.path.insert(0, os.path.join(SKILL, "scripts"))
    os.environ["JY_SKILL_ROOT"] = SKILL
    from jy_wrapper import JyProject
    sp = spec["style_profile"]
    p = JyProject(title, width=sp["resolution"]["w"], height=sp["resolution"]["h"],
                  overwrite=True)
    if narration:
        p.add_narrated_subtitles(narration, speaker="zh_female_xiaopengyou")
    media = []
    for ext in ("*.mp4", "*.mov", "*.jpg", "*.png"):
        media += glob.glob(os.path.join(media_dir, ext))
    for m in media[:8]:
        try:
            p.add_media_safe(m)
        except Exception:
            pass
    p.save()
    print("  ✔ 剪映草稿已生成（重启剪映可见）")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python replica_builder.py <spec.json> <素材文件夹> "
              "[--title 标题] [--narration 文本/txt] [--bgm 文件] [--out 成品]")
        sys.exit(1)
    spec_path, media_dir = sys.argv[1], sys.argv[2]
    args = sys.argv[3:]
    title, narration, bgm, out = "我的复刻作品", None, None, "成品"
    i = 0
    while i < len(args):
        if args[i] == "--title": title = args[i + 1]; i += 2
        elif args[i] == "--narration":
            v = args[i + 1]; i += 2
            narration = (open(v, encoding="utf-8").read()
                         if os.path.exists(v) and v.endswith(".txt") else v)
        elif args[i] == "--bgm": bgm = args[i + 1]; i += 2
        elif args[i] == "--out": out = args[i + 1]; i += 2
        else: i += 1
    build(spec_path, media_dir, title, narration, bgm, out)
