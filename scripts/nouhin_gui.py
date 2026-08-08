#!/usr/bin/env python3
"""納品前チェック D&D用GUI — Mac / Windows 共通。

ドロップレット（Mac: 納品チェック.app / Windows: nouhin-check.bat）から
ファイルパスを引数で受け取り、納品先をドロップダウンで選ばせて測定し、
HTMLレポートを生成してブラウザで開く。

測定ロジックは check.py の measure_file() をそのまま使う（基準は check.py が単一ソース）。

テスト・自動実行用:
    python3 nouhin_gui.py <files...> --no-gui --target broadcast --duration 30
"""

import os
import platform
import shutil
import subprocess
import sys
import webbrowser
from datetime import datetime
from html import escape
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import check  # noqa: E402  （measure_file / TARGETS を使う）

VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".avi", ".mkv", ".webm", ".m4v", ".mts", ".m2ts", ".wav", ".mp3", ".aac", ".m4a"}

# ドロップレット（Finder / Explorer）から起動すると PATH が最小構成のことがあるので補う
if platform.system() == "Darwin":
    os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")


def ffmpeg_missing_message():
    if platform.system() == "Windows":
        return (
            "ffmpeg が見つかりません。\n\n"
            "PowerShell で以下を実行してインストールしてください:\n\n"
            "    winget install Gyan.FFmpeg\n\n"
            "インストール後、一度サインアウト（または再起動）すると使えるようになります。"
        )
    return (
        "ffmpeg が見つかりません。\n\n"
        "ターミナルで以下を実行してインストールしてください:\n\n"
        "    brew install ffmpeg"
    )


# ---------------------------------------------------------------- HTML レポート

CSS = """
body { font-family: 'Hiragino Sans', 'Yu Gothic UI', Meiryo, sans-serif;
       background: #f4f5f7; color: #1a1d21; margin: 0; padding: 32px 16px; }
.wrap { max-width: 860px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; }
.meta { color: #667; font-size: 13px; margin-bottom: 20px; }
.summary { padding: 14px 18px; border-radius: 10px; font-size: 15px; font-weight: 600;
           margin-bottom: 24px; }
.summary.ok { background: #e6f4ea; color: #1e7e34; }
.summary.ng { background: #fdecea; color: #c0392b; }
.card { background: #fff; border-radius: 12px; padding: 20px 24px; margin-bottom: 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.card h2 { font-size: 16px; margin: 0 0 2px; word-break: break-all; }
.badge { display: inline-block; font-size: 12px; font-weight: 700; padding: 3px 10px;
         border-radius: 999px; margin-left: 8px; vertical-align: 2px; }
.badge.ok { background: #e6f4ea; color: #1e7e34; }
.badge.ng { background: #fdecea; color: #c0392b; }
.path { color: #99a; font-size: 11px; margin-bottom: 12px; word-break: break-all; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 5px 10px; border-bottom: 1px solid #eef0f2; }
th { color: #667; font-weight: 600; white-space: nowrap; width: 11em; }
.v-ok { color: #1e7e34; font-weight: 700; }
.v-ng { color: #c0392b; font-weight: 700; }
.v-na { color: #99a; }
.issues { margin: 14px 0 0; padding: 12px 16px; background: #fdecea; border-radius: 8px; }
.issues li { color: #c0392b; font-size: 13px; margin: 3px 0 3px 1em; }
.spans { color: #556; font-size: 12px; margin: 2px 0 0; }
.spans .mid { color: #c0392b; font-weight: 700; }
h3 { font-size: 13px; color: #445; margin: 18px 0 6px; }
.err { color: #c0392b; font-size: 13px; }
footer { color: #aab; font-size: 11px; text-align: center; margin-top: 28px; }
"""


def _mark(ok):
    return '<span class="v-ok">✓</span>' if ok else '<span class="v-ng">✗</span>'


def _spans_html(spans, duration, mid_is_bad):
    tags = {"head": "頭", "tail": "尻", "mid": "途中"}
    parts = []
    for x in spans[:8]:
        cls = "mid" if (mid_is_bad and x["pos"] == "mid") else ""
        parts.append(
            f'<div class="spans"><span class="{cls}">'
            f'{x["start"]:.2f}s 〜 {x["end"]:.2f}s（{x["end"] - x["start"]:.2f}秒 / {tags[x["pos"]]}）</span></div>'
        )
    if len(spans) > 8:
        parts.append(f'<div class="spans">…他 {len(spans) - 8}箇所</div>')
    return "".join(parts)


def file_card(r):
    if r.get("error"):
        return (
            f'<div class="card"><h2>{escape(r["name"])}'
            f'<span class="badge ng">解析失敗</span></h2>'
            f'<div class="path">{escape(r["path"])}</div>'
            f'<p class="err">{escape(r["error"])}</p></div>'
        )

    ok = not r["issues"]
    badge = '<span class="badge ok">問題なし</span>' if ok else f'<span class="badge ng">要確認 {len(r["issues"])}件</span>'
    rows = []

    v = r["video"]
    if v:
        fps = f'{v["fps"]:.3f} fps' if v["fps"] else "不明"
        rows.append(f'<tr><th>解像度 / fps</th><td>{v["width"]}×{v["height"]} / {fps}</td></tr>')
        codec = f'{v["codec"]} / {v["profile"]} / {v["pix_fmt"]}'
        if v["mbps"]:
            codec += f' / {v["mbps"]:.2f} Mbps'
        rows.append(f'<tr><th>コーデック</th><td>{escape(codec)}</td></tr>')

    dur = check.fmt_dur(r["duration"])
    if r["want_duration"] is not None:
        dur += f' {_mark(r["duration_ok"])} 指定 {r["want_duration"]}秒 に対し {r["duration_diff"]:+.2f}秒'
    rows.append(f'<tr><th>尺</th><td>{dur}</td></tr>')
    rows.append(f'<tr><th>ファイル</th><td>{r["size_mb"]:.1f} MB / {escape(r["format_name"])}</td></tr>')

    a = r["audio"]
    if a:
        rows.append(f'<tr><th>音声</th><td>{a["codec"]} / {a["sample_rate"]} Hz / {a["channels"]}ch</td></tr>')
        ln = r["loudness"]
        t = r["target"]
        if ln:
            rows.append(
                f'<tr><th>統合ラウドネス</th><td>{_mark(ln["ok_i"])} {ln["i"]:.1f} LUFS'
                f'（目標 {t["lufs"]:.1f} ±{t["tol"]:.0f} / 差 {ln["diff_i"]:+.1f}）</td></tr>'
            )
            rows.append(
                f'<tr><th>トゥルーピーク</th><td>{_mark(ln["ok_tp"])} {ln["tp"]:.1f} dBTP'
                f'（上限 {t["tp"]:.1f}）</td></tr>'
            )
            rows.append(f'<tr><th>ラウドネスレンジ</th><td><span class="v-na">―</span> {ln["lra"]:.1f} LU</td></tr>')
        else:
            rows.append('<tr><th>ラウドネス</th><td><span class="v-na">△ 測定できませんでした</span></td></tr>')
    else:
        rows.append(f'<tr><th>音声</th><td>{_mark(False)} 音声ストリームなし</td></tr>')

    sil = r["silences"]
    sil_html = _spans_html(sil, r["duration"], mid_is_bad=False) if sil else ""
    rows.append(
        f'<tr><th>無音区間</th><td>{"なし " + _mark(True) if not sil else f"{len(sil)}箇所"}{sil_html}</td></tr>'
    )
    if v:
        blk = r["blacks"]
        blk_html = _spans_html(blk, r["duration"], mid_is_bad=True) if blk else ""
        rows.append(
            f'<tr><th>黒フレーム</th><td>{"なし " + _mark(True) if not blk else f"{len(blk)}箇所"}{blk_html}</td></tr>'
        )

    issues_html = ""
    if r["issues"]:
        lis = "".join(f"<li>{escape(s)}</li>" for s in r["issues"])
        issues_html = f'<ul class="issues">{lis}</ul>'

    return (
        f'<div class="card"><h2>{escape(r["name"])}{badge}</h2>'
        f'<div class="path">{escape(r["path"])}</div>'
        f'<table>{"".join(rows)}</table>{issues_html}</div>'
    )


def build_report(results, target_key, want_duration):
    t = check.TARGETS[target_key]
    now = datetime.now()
    total = sum(len(r.get("issues", [])) + (1 if r.get("error") else 0) for r in results)
    cls = "ok" if total == 0 else "ng"
    verdict = "✓ 全ファイル問題なし" if total == 0 else f"✗ 要確認 {total}件"
    dur_note = f"／指定尺 {want_duration}秒" if want_duration is not None else ""
    cards = "".join(file_card(r) for r in results)
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<title>納品チェックレポート {now:%Y-%m-%d %H:%M}</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>納品チェックレポート</h1>
<div class="meta">{now:%Y-%m-%d %H:%M}　納品先基準: {escape(t["label"])}（{t["lufs"]:.0f} LUFS ±{t["tol"]:.0f} / TP {t["tp"]:.1f} dBTP）{dur_note}　{len(results)}ファイル</div>
<div class="summary {cls}">{verdict}</div>
{cards}
<footer>nouhin-check — 機械で測れる項目のみ。最終確認は人の目と耳で。</footer>
</div></body></html>"""


def save_and_open(html, first_file):
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    name = f"{stamp}_納品チェック.html"
    try:
        out = Path(first_file).expanduser().resolve().parent / name
        out.write_text(html, encoding="utf-8")
    except OSError:
        out = Path.home() / name
        out.write_text(html, encoding="utf-8")
    webbrowser.open(out.as_uri())
    return out


def run_checks(files, target_key, want_duration, on_progress=None):
    results = []
    for n, f in enumerate(files, 1):
        p = Path(f).expanduser()
        if on_progress:
            on_progress(n, len(files), p.name)
        try:
            results.append(check.measure_file(p, target_key, want_duration))
        except Exception as e:  # noqa: BLE001 — 1ファイルの失敗で全体を止めない
            results.append({"name": p.name, "path": str(p), "error": str(e), "issues": []})
    return results


# ---------------------------------------------------------------- GUI

TARGET_CHOICES = [
    ("YouTube / SNS（-14 LUFS）", "youtube"),
    ("TV放送・CM納品（-24 LKFS / ARIB）", "broadcast"),
    ("Web広告 汎用（-16 LUFS）", "web"),
]


def run_gui(files):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("納品チェック")
    root.resizable(False, False)
    try:  # 前面に出す（ドロップレット経由だと背面に開くことがある）
        root.attributes("-topmost", True)
        root.after(500, lambda: root.attributes("-topmost", False))
    except tk.TclError:
        pass

    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        messagebox.showerror("納品チェック", ffmpeg_missing_message())
        root.destroy()
        return 1

    if not files:
        files = list(filedialog.askopenfilenames(
            title="チェックする動画を選択",
            filetypes=[("動画・音声", " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))), ("すべて", "*.*")],
        ))
        if not files:
            root.destroy()
            return 0

    frm = ttk.Frame(root, padding=16)
    frm.grid()

    ttk.Label(frm, text=f"チェック対象: {len(files)}ファイル", font=("", 13, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w")
    names = "\n".join(Path(f).name for f in files[:6]) + ("\n…" if len(files) > 6 else "")
    ttk.Label(frm, text=names, foreground="#556").grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(2, 12))

    ttk.Label(frm, text="納品先:").grid(row=2, column=0, sticky="w")
    target_var = tk.StringVar(value=TARGET_CHOICES[0][0])
    combo = ttk.Combobox(frm, textvariable=target_var, state="readonly", width=34,
                         values=[c[0] for c in TARGET_CHOICES])
    combo.grid(row=2, column=1, sticky="w", pady=2)

    ttk.Label(frm, text="指定尺（秒・任意）:").grid(row=3, column=0, sticky="w")
    dur_var = tk.StringVar()
    ttk.Entry(frm, textvariable=dur_var, width=8).grid(row=3, column=1, sticky="w", pady=2)
    ttk.Label(frm, text="CM・広告枠など尺が決まっている時だけ入力（例: 30）",
              foreground="#889", font=("", 10)).grid(row=4, column=0, columnspan=2, sticky="w")

    status = ttk.Label(frm, text="", foreground="#556")
    status.grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))

    btn = ttk.Button(frm, text="チェック開始")
    btn.grid(row=6, column=0, columnspan=2, pady=(12, 0))

    def start():
        raw = dur_var.get().strip()
        want = None
        if raw:
            try:
                want = float(raw)
            except ValueError:
                messagebox.showwarning("納品チェック", "指定尺は数字（秒）で入力してください。例: 30")
                return
        target_key = dict(TARGET_CHOICES)[target_var.get()]
        btn.state(["disabled"])
        combo.state(["disabled"])

        def progress(n, total, name):
            status.config(text=f"測定中 {n}/{total}: {name}")
            root.update()

        results = run_checks(files, target_key, want, on_progress=progress)
        out = save_and_open(build_report(results, target_key, want), files[0])
        status.config(text=f"完了。レポートをブラウザで開きました。\n{out}")
        btn.config(text="閉じる", command=root.destroy)
        btn.state(["!disabled"])

    btn.config(command=start)
    root.mainloop()
    return 0


# ---------------------------------------------------------------- main

def main():
    argv = sys.argv[1:]
    no_gui = "--no-gui" in argv
    target_key = "youtube"
    want = None
    files = []
    it = iter(argv)
    for a in it:
        if a == "--no-gui":
            continue
        if a == "--target":
            target_key = next(it)
        elif a == "--duration":
            want = float(next(it))
        else:
            files.append(a)

    if no_gui:
        if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
            print(ffmpeg_missing_message())
            return 1
        results = run_checks(files, target_key, want,
                             on_progress=lambda n, t, name: print(f"測定中 {n}/{t}: {name}"))
        out = save_and_open(build_report(results, target_key, want), files[0])
        print(f"レポート: {out}")
        return 0

    return run_gui(files)


if __name__ == "__main__":
    sys.exit(main())
