# -*- coding: utf-8 -*-
"""
跨平台外部工具探测 (paths)
==========================
本文件为 deconstruct-rebuild-workbench 新增代码（基于 jianying-editor MIT 技能的运行时二次开发）。

解析优先级（每个工具都一样）：
  1. 环境变量显式指定
  2. PATH 自动探测 (shutil.which)
  3. WinGet Links shim / 已知安装位置
  4. 退回裸命令名（指望调用者的 PATH）

支持的环境变量：
  FFMPEG_PATH     ffmpeg 可执行文件完整路径
  FFPROBE_PATH    ffprobe 可执行文件完整路径
  TESSERACT_PATH  tesseract 可执行文件完整路径（OCR 字幕识别用）
  JY_SKILL_DIR    jianying-editor skill 根目录（完整包布局下自动指向仓库内 ../jianying-editor）
  JY_PYTHON       跑子进程脚本用的 Python 解释器（默认用当前解释器）
"""
import os
import sys
import glob
import shutil


def _env(name):
    v = os.environ.get(name)
    return v if v and os.path.isfile(v) else None


def _which(name):
    try:
        return shutil.which(name)
    except Exception:
        return None


def _winget_link(name):
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                        "Microsoft", "WinGet", "Links")
    p = os.path.join(base, name + (".exe" if os.name == "nt" else ""))
    return p if os.path.isfile(p) else None


def _glob_first(patterns):
    for pat in patterns:
        if not pat:
            continue
        for p in sorted(glob.glob(pat, recursive=True), reverse=True):
            if os.path.isfile(p):
                return p
    return None


def find_ffmpeg():
    return (_env("FFMPEG_PATH") or _which("ffmpeg") or _winget_link("ffmpeg")
            or _glob_first([
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft",
                             "WinGet", "Packages", "Gyan.FFmpeg*", "**",
                             "bin", "ffmpeg.exe"),
                "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg",
                "/opt/homebrew/bin/ffmpeg",
            ])
            or "ffmpeg")


def find_ffprobe():
    p = _env("FFPROBE_PATH") or _which("ffprobe")
    if p:
        return p
    ff = find_ffmpeg()
    if os.path.isfile(ff):
        cand = os.path.join(os.path.dirname(ff),
                            "ffprobe.exe" if os.name == "nt" else "ffprobe")
        if os.path.isfile(cand):
            return cand
    return (_glob_first([
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft",
                     "WinGet", "Packages", "Gyan.FFmpeg*", "**",
                     "bin", "ffprobe.exe"),
        "/usr/bin/ffprobe", "/usr/local/bin/ffprobe",
        "/opt/homebrew/bin/ffprobe",
    ])
        or "ffprobe")


def find_tesseract():
    known = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Tesseract-OCR", "tesseract.exe"),
        "/usr/bin/tesseract", "/usr/local/bin/tesseract",
        "/opt/homebrew/bin/tesseract",
    ]
    for p in known:
        if p and os.path.isfile(p):
            return p
    return (_env("TESSERACT_PATH") or _which("tesseract")
            or _winget_link("tesseract") or "tesseract")


def find_skill_dir():
    """jianying-editor skill 根目录（内含 scripts/vendor/pyJianYingDraft）"""
    v = os.environ.get("JY_SKILL_DIR")
    if v and os.path.isdir(v):
        return v
    # 完整包布局：本文件位于 <repo>/workbench/paths.py，skill 在 <repo>/jianying-editor
    local = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "..", "jianying-editor"))
    if os.path.isdir(local):
        return local
    home = os.path.join(os.path.expanduser("~"), ".workbuddy", "skills",
                        "jianying-editor")
    if os.path.isdir(home):
        return home
    return local


FFMPEG = find_ffmpeg()
FFPROBE = find_ffprobe()
TESSERACT = find_tesseract()
SKILL = find_skill_dir()
PYTHON = os.environ.get("JY_PYTHON") or sys.executable
