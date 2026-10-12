# -*- coding: utf-8 -*-
# 本文件为 deconstruct-rebuild-workbench 新增代码（MIT），基于 jianying-editor 技能运行时二次开发，详见 NOTICE.md
"""
网页版工作台 (Web Server)
========================
在浏览器里完成「丢对标 → 提要求 → 看成片」：

  1. python web_server.py                （默认 127.0.0.1:8000，可用第一个参数改端口）
  2. 浏览器打开 http://127.0.0.1:8000

设计约束：
  - 只监听 127.0.0.1，不暴露局域网/公网
  - 拆解+复刻是分钟级长任务，放后台线程执行，日志实时转发，前端轮询状态
  - 上传与输出按 job 隔离在 web_uploads/ 与 web_out/ 下（均已在 .gitignore）
"""
import os
import sys
import glob
import uuid
import time
import threading
import subprocess

from flask import (Flask, request, jsonify, send_file,
                   send_from_directory, abort)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import PYTHON  # noqa: E402

WEB_DIR = os.path.join(HERE, "web")
UPLOAD_DIR = os.path.join(HERE, "web_uploads")
OUTPUT_DIR = os.path.join(HERE, "web_out")

for _d in (UPLOAD_DIR, OUTPUT_DIR):
    os.makedirs(_d, exist_ok=True)

app = Flask(__name__)

JOBS = {}            # job_id -> {status, log[], outputs[], error, created, title}
JOBS_LOCK = threading.Lock()

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi", ".flv", ".wmv", ".m4v")
AUDIO_EXT = (".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac")

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.post("/api/upload")
def upload():
    """上传单个文件。type=ref(对标视频) / bgm(音乐) / footage(素材，可多个)"""
    job_id = request.args.get("job") or uuid.uuid4().hex[:12]
    ftype = request.form.get("type", "footage")
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="没有收到文件"), 400
    sub = {"ref": "ref", "bgm": "bgm"}.get(ftype, "footage")
    dst_dir = os.path.join(UPLOAD_DIR, job_id, sub)
    os.makedirs(dst_dir, exist_ok=True)
    if sub != "footage":                       # ref / bgm 只保留最新一个
        for old in glob.glob(os.path.join(dst_dir, "*")):
            os.remove(old)
    safe = os.path.basename(f.filename)
    if sub == "footage":                       # 素材防重名覆盖
        safe = f"{uuid.uuid4().hex[:6]}_{safe}"
    f.save(os.path.join(dst_dir, safe))
    return jsonify(job=job_id, name=os.path.basename(f.filename), type=sub)


@app.post("/api/run")
def run():
    data = request.get_json(force=True)
    job_id = data.get("job") or ""
    up = os.path.join(UPLOAD_DIR, job_id)
    refs = glob.glob(os.path.join(up, "ref", "*"))
    media = os.path.join(up, "footage")
    if not refs:
        return jsonify(error="还没上传对标视频"), 400
    if not os.path.isdir(media) or not os.listdir(media):
        return jsonify(error="还没上传素材"), 400
    with JOBS_LOCK:
        if JOBS.get(job_id, {}).get("status") == "running":
            return jsonify(error="该任务正在运行中"), 409
        JOBS[job_id] = {"status": "running", "log": [], "outputs": [],
                        "error": "", "created": time.strftime("%m-%d %H:%M"),
                        "title": data.get("title") or "我的复刻作品"}
    threading.Thread(target=_run_job,
                     args=(job_id, data, refs[0], media), daemon=True).start()
    return jsonify(job=job_id, status="running")


def _run_job(job_id, data, ref, media):
    job = JOBS[job_id]
    out_dir = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(os.path.join(out_dir, "成品"), exist_ok=True)
    cmd = [PYTHON, os.path.join(HERE, "workbench.py"), "auto", ref, media,
           "--title", data.get("title") or "我的复刻作品",
           "--out", os.path.join(out_dir, "成品"),
           "--specs-dir", os.path.join(out_dir, "specs"),
           "--end", data.get("end") or "感谢观看"]
    if data.get("narration"):
        cmd += ["--narration", data["narration"]]
    bgms = glob.glob(os.path.join(UPLOAD_DIR, job_id, "bgm", "*"))
    if bgms:
        cmd += ["--bgm", bgms[0]]
    job["log"].append("$ workbench auto " + os.path.basename(ref))
    try:
        proc = subprocess.Popen(
            cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace", bufsize=1,
            creationflags=_CREATE_NO_WINDOW)
        for line in proc.stdout:
            job["log"].append(line.rstrip("\r\n"))
        rc = proc.wait()
        if rc == 0:
            job["status"] = "done"
            job["outputs"] = sorted(
                glob.glob(os.path.join(out_dir, "成品", "*.mp4")))
            job["log"].append("全部完成，可在下方播放/下载")
        else:
            job["status"] = "error"
            job["error"] = f"子进程退出码 {rc}"
            job["log"].append(f"失败（退出码 {rc}），请检查上方日志")
    except Exception as e:                     # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)
        job["log"].append(f"异常：{e}")


@app.get("/api/status/<job_id>")
def status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(error="任务不存在"), 404
        return jsonify(status=job["status"],
                       log="\n".join(job["log"][-400:]),
                       outputs=[os.path.basename(p) for p in job["outputs"]],
                       error=job["error"], title=job["title"],
                       created=job["created"])


@app.get("/api/history")
def history():
    items = []
    for jid in sorted(os.listdir(OUTPUT_DIR)):
        for p in sorted(glob.glob(os.path.join(OUTPUT_DIR, jid, "成品", "*.mp4"))):
            items.append({"job": jid, "name": os.path.basename(p),
                          "size": f"{os.path.getsize(p) / 1048576:.1f} MB",
                          "mtime": os.path.getmtime(p)})
    return jsonify(items=sorted(items, key=lambda x: x["mtime"], reverse=True))


@app.get("/api/video/<job_id>/<path:fname>")
def video(job_id, fname):
    p = os.path.join(OUTPUT_DIR, job_id, "成品", fname)
    if not os.path.isfile(p):
        abort(404)
    return send_file(p, conditional=True)      # 支持 Range，可拖动进度条


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print("\n  对标复刻工作台 · 网页版")
    print(f"  浏览器打开: http://127.0.0.1:{port}")
    print("  （只监听本机；关闭此窗口即停止服务）\n")
    app.run(host="127.0.0.1", port=port, threaded=True)
