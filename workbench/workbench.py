# -*- coding: utf-8 -*-
# 本文件为 deconstruct-rebuild-workbench 新增代码（MIT），基于 jianying-editor 技能运行时二次开发，详见 NOTICE.md
"""
自动化剪辑工作台 · 对标复刻模式
================================
把「先找对标 → 再拆解 → 投喂素材复刻」串成一条命令链。

子命令：
  analyze   <参考视频> [--out specs]
      拆解参考视频的剪辑手法，产出三份文件：
        specs/<名>_spec.json       机器可读时间轴（复刻引擎消费）
        specs/<名>_report.md       人话版「几分几秒用了什么」
        specs/<名>_storyboard.md   拆解后的拍摄建议/分镜表（拍哪些素材、怎么拍）

  replicate <spec.json> <素材文件夹> [--title 标题] [--narration 文本/txt]
            [--bgm 音乐文件] [--out 成品] [--end 片尾文字]
      按对标复刻：镜头数/转场/节奏/字幕样式/BGM 摆放自动还原，
      旁白自动 AI 配音 + 逐句字幕（时间轴与配音严格对齐）。
      产出：成品/<名>_复刻.mp4 + 剪映草稿（可在剪映继续精修）

  auto     <参考视频> <素材文件夹> [--title ...] [--narration ...]
      全自动一条龙：先 analyze 再 replicate，中间无需手动。

  menu     交互式选择（适合不熟悉命令行的人）

自然语言触发（在 WorkBuddy 会话里直接说）：
  “对标分析这个视频 D:\ref.mp4”
  “按 D:\ref.mp4 的剪辑手法复刻，素材在 D:\footage，标题‘周末露营’，旁白：……”
"""
import os, sys, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
from paths import PYTHON as PY


def analyze(ref, out="specs"):
    print(f"\n===== [第 1 步] 对标拆解：{ref} =====")
    cmd = [PY, os.path.join(HERE, "reference_analyzer.py"), ref, "--out", out]
    rc = subprocess.run(cmd).returncode
    if rc == 0:
        stem = os.path.splitext(os.path.basename(ref))[0]
        print(f"\n📄 拆解完成，请看这三份文件：")
        print(f"   {os.path.join(out, stem + '_report.md')}      ← 人话版「几分几秒用了什么」")
        print(f"   {os.path.join(out, stem + '_storyboard.md')}  ← 拍摄建议：拍哪些素材、怎么拍")
        print(f"   {os.path.join(out, stem + '_spec.json')}      ← 复刻引擎的输入")
    return rc


def replicate(spec, media, title="我的复刻作品", narration=None, bgm=None,
              out="成品", end_text="感谢观看"):
    print(f"\n===== [第 2 步] 投喂素材复刻：{media} =====")
    cmd = [PY, os.path.join(HERE, "replica_builder.py"), spec, media,
           "--title", title, "--out", out, "--end", end_text]
    if narration:
        cmd += ["--narration", narration]
    if bgm:
        cmd += ["--bgm", bgm]
    return subprocess.run(cmd).returncode


def auto(ref, media, title="我的复刻作品", narration=None, bgm=None,
         out="成品", end_text="感谢观看", specs_dir="specs"):
    """全自动：先拆解，再直接复刻"""
    rc = analyze(ref, specs_dir)
    if rc != 0:
        return rc
    stem = os.path.splitext(os.path.basename(ref))[0]
    spec = os.path.join(specs_dir, stem + "_spec.json")
    return replicate(spec, media, title, narration, bgm, out, end_text)


def menu():
    print("\n" + "=" * 56)
    print("  自动化剪辑工作台 · 对标复刻模式")
    print("  流程：先找对标 → 拆解手法 → 拍/喂素材 → 复刻成片")
    print("=" * 56)
    print("1) 对标分析：拆一个参考视频的剪辑手法（含拍摄建议）")
    print("2) 复刻：投喂素材，按对标复刻成片")
    print("3) 全自动：对标 + 复刻一条龙")
    print("0) 退出")
    while True:
        c = input("\n选择 > ").strip()
        if c == "0":
            break
        elif c == "1":
            ref = input("参考视频路径 > ").strip().strip('"')
            if ref:
                analyze(ref)
        elif c == "2":
            spec = input("spec.json 路径 > ").strip().strip('"')
            media = input("素材文件夹 > ").strip().strip('"')
            title = input("成片标题(回车默认) > ").strip() or "我的复刻作品"
            nar = input("旁白文案/txt路径(可回车跳过) > ").strip().strip('"')
            if nar and os.path.exists(nar) and nar.endswith(".txt"):
                nar = open(nar, encoding="utf-8").read()
            replicate(spec, media, title, nar or None)
        elif c == "3":
            ref = input("参考视频路径 > ").strip().strip('"')
            media = input("素材文件夹 > ").strip().strip('"')
            title = input("成片标题(回车默认) > ").strip() or "我的复刻作品"
            nar = input("旁白文案/txt路径(可回车跳过) > ").strip().strip('"')
            if nar and os.path.exists(nar) and nar.endswith(".txt"):
                nar = open(nar, encoding="utf-8").read()
            auto(ref, media, title, nar or None)
        else:
            print("无效选择")


def _parse(args):
    """解析 --key value 形式的参数"""
    opts, rest = {}, []
    i = 0
    while i < len(args):
        if args[i].startswith("--"):
            opts[args[i][2:].replace("-", "_")] = args[i + 1] if i + 1 < len(args) else ""
            i += 2
        else:
            rest.append(args[i])
            i += 1
    return opts, rest


if __name__ == "__main__":
    if len(sys.argv) < 2:
        menu()
        sys.exit(0)
    cmd = sys.argv[1]
    opts, rest = _parse(sys.argv[2:])
    if cmd == "analyze" and rest:
        sys.exit(analyze(rest[0], opts.get("out", "specs")) or 0)
    elif cmd == "replicate" and len(rest) >= 2:
        sys.exit(replicate(rest[0], rest[1],
                           opts.get("title", "我的复刻作品"),
                           opts.get("narration"), opts.get("bgm"),
                           opts.get("out", "成品"),
                           opts.get("end", "感谢观看")) or 0)
    elif cmd == "auto" and len(rest) >= 2:
        sys.exit(auto(rest[0], rest[1],
                      opts.get("title", "我的复刻作品"),
                      opts.get("narration"), opts.get("bgm"),
                      opts.get("out", "成品"),
                      opts.get("end", "感谢观看"),
                      opts.get("specs_dir", "specs")) or 0)
    elif cmd == "menu":
        menu()
    else:
        print(__doc__)
