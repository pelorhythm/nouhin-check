#!/usr/bin/env python3
"""納品前チェック — 動画ファイルの仕様とラウドネスを実測して一覧で返す。

使い方:
    python3 check.py <動画ファイル> [...] [--target youtube|broadcast|web] [--duration 30]

ffprobe / ffmpeg を使うので、事前に brew install ffmpeg が必要（導入済み）。
判定基準は ~/.claude/skills/Video_Skills/audio-and-music.md に準拠。

measure_file() が測定結果を dict で返し、check_file() がそれをターミナル表示する。
nouhin_gui.py（D&D用GUI）も同じ measure_file() を使うので、基準の変更はこのファイルだけで済む。
"""

import argparse
import json
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

# 納品先ごとのラウドネス基準（audio-and-music.md より）
TARGETS = {
    "youtube": {"label": "YouTube / SNS", "lufs": -14.0, "tol": 1.0, "tp": -1.0},
    "broadcast": {"label": "日本のTV放送 (ARIB TR-B32)", "lufs": -24.0, "tol": 1.0, "tp": -1.0},
    "web": {"label": "Web広告 汎用", "lufs": -16.0, "tol": 1.0, "tp": -1.0},
}

OK, NG, WARN, NA = "✓", "✗", "△", "―"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")


def probe(path):
    r = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size,format_name,bit_rate",
        "-show_entries", "stream=index,codec_type,codec_name,profile,width,height,"
                         "r_frame_rate,pix_fmt,bit_rate,sample_rate,channels",
        "-of", "json", str(path),
    ])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ffprobe に失敗しました")
    return json.loads(r.stdout)


def analyze(path, has_audio, has_video):
    """1パスで黒フレーム・無音・ラウドネスを測る。"""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path)]
    if has_video:
        cmd += ["-vf", "blackdetect=d=0.1:pix_th=0.10"]
    if has_audio:
        cmd += ["-af", "silencedetect=n=-50dB:d=0.5,loudnorm=print_format=json"]
    cmd += ["-f", "null", "-"]
    return run(cmd).stderr


def parse_loudnorm(log):
    """loudnorm が最後に吐く JSON ブロックを拾う。"""
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", log, re.S)
    if not blocks:
        return None
    try:
        return json.loads(blocks[-1])
    except json.JSONDecodeError:
        return None


def parse_black(log):
    return [
        (float(s), float(e))
        for s, e in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)
    ]


def parse_silence(log):
    out, start = [], None
    for m in re.finditer(r"silence_(start|end): (-?[\d.]+)", log):
        kind, val = m.group(1), float(m.group(2))
        if kind == "start":
            start = val
        elif start is not None:
            out.append((start, val))
            start = None
    return out


def fps_of(stream):
    try:
        f = Fraction(stream.get("r_frame_rate", "0/1"))
        return float(f) if f else None
    except (ZeroDivisionError, ValueError):
        return None


def fmt_dur(sec):
    m, s = divmod(sec, 60)
    return f"{int(m):02d}:{s:05.2f}（{sec:.2f}秒）"


def _pos(start, end, duration):
    """区間がファイルの頭・尻・途中のどこにあるか。"""
    if start < 0.5:
        return "head"
    if end > duration - 0.5:
        return "tail"
    return "mid"


def measure_file(path, target, want_duration):
    """1ファイルを測定して結果を dict で返す。表示はしない。"""
    info = probe(path)
    fmt = info.get("format", {})
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = float(fmt.get("duration", 0) or 0)
    t = TARGETS[target]
    issues = []

    r = {
        "name": path.name,
        "path": str(path),
        "duration": duration,
        "size_mb": int(fmt.get("size", 0) or 0) / 1_048_576,
        "format_name": fmt.get("format_name", "-"),
        "target": {"key": target, **t},
        "video": None,
        "audio": None,
        "want_duration": want_duration,
        "duration_diff": None,
        "duration_ok": None,
        "loudness": None,
        "silences": [],
        "blacks": [],
    }

    if v:
        r["video"] = {
            "width": v.get("width"),
            "height": v.get("height"),
            "fps": fps_of(v),
            "codec": v.get("codec_name"),
            "profile": v.get("profile", "-"),
            "pix_fmt": v.get("pix_fmt", "-"),
            "mbps": int(v["bit_rate"]) / 1_000_000 if v.get("bit_rate") else None,
        }

    if want_duration is not None:
        diff = duration - want_duration
        r["duration_diff"] = diff
        r["duration_ok"] = abs(diff) <= 0.1
        if not r["duration_ok"]:
            issues.append(f"尺が指定より {diff:+.2f}秒 ずれている")

    if not a:
        issues.append("音声ストリームなし")
        log = analyze(path, False, bool(v))
    else:
        r["audio"] = {
            "codec": a.get("codec_name"),
            "sample_rate": a.get("sample_rate"),
            "channels": a.get("channels"),
        }
        log = analyze(path, True, bool(v))
        ln = parse_loudnorm(log)
        if ln:
            i = float(ln["input_i"])
            tp = float(ln["input_tp"])
            lra = float(ln["input_lra"])
            di = i - t["lufs"]
            r["loudness"] = {
                "i": i, "tp": tp, "lra": lra, "diff_i": di,
                "ok_i": abs(di) <= t["tol"],
                "ok_tp": tp <= t["tp"],
            }
            if not r["loudness"]["ok_i"]:
                issues.append(f"ラウドネスが目標から {di:+.1f} LU ずれている（{i:.1f} → {t['lufs']:.1f} へ要調整）")
            if not r["loudness"]["ok_tp"]:
                issues.append(f"トゥルーピークが {tp:.1f} dBTP で上限超過（歪みリスク）")

        for s, e in parse_silence(log):
            r["silences"].append({"start": s, "end": e, "pos": _pos(s, e, duration)})
        if any(x["pos"] == "head" for x in r["silences"]):
            issues.append("頭に無音がある（頭切れ・尺調整の確認）")

    if v:
        for s, e in parse_black(log):
            r["blacks"].append({"start": s, "end": e, "pos": _pos(s, e, duration)})
        if any(x["pos"] == "mid" for x in r["blacks"]):
            issues.append("途中に黒フレームがある（カット抜け・レンダリング事故の疑い）")

    r["issues"] = issues
    return r


def check_file(path, target, want_duration):
    """1ファイルを測定してターミナルに表示。要確認件数を返す。"""
    r = measure_file(path, target, want_duration)
    print(f"\n{'=' * 62}\n■ {r['name']}\n{'=' * 62}")

    # --- 映像 ---
    print("\n【映像】")
    v = r["video"]
    if v:
        print(f"  解像度      : {v['width']}×{v['height']}")
        print(f"  フレームレート: {v['fps']:.3f} fps" if v["fps"] else "  フレームレート: 不明")
        print(f"  コーデック   : {v['codec']} / {v['profile']} / {v['pix_fmt']}")
        if v["mbps"]:
            print(f"  ビットレート : {v['mbps']:.2f} Mbps")
    else:
        print("  （映像ストリームなし）")

    # --- 尺 ---
    print("\n【尺】")
    print(f"  実測        : {fmt_dur(r['duration'])}")
    print(f"  ファイルサイズ: {r['size_mb']:.1f} MB / {r['format_name']}")
    if r["want_duration"] is not None:
        mark = OK if r["duration_ok"] else NG
        print(f"  指定尺との差 : {mark} 指定 {r['want_duration']}秒 に対し {r['duration_diff']:+.2f}秒")

    # --- 音声 ---
    print("\n【音声】")
    a = r["audio"]
    if not a:
        print(f"  {NG} 音声ストリームがありません")
    else:
        print(f"  コーデック   : {a['codec']} / {a['sample_rate']} Hz / {a['channels']}ch")
        t = r["target"]
        print(f"\n【ラウドネス】基準: {t['label']}")
        ln = r["loudness"]
        if ln:
            mi = OK if ln["ok_i"] else NG
            mtp = OK if ln["ok_tp"] else NG
            print(f"  {mi} 統合ラウドネス: {ln['i']:.1f} LUFS （目標 {t['lufs']:.1f} ±{t['tol']:.0f} / 差 {ln['diff_i']:+.1f}）")
            print(f"  {mtp} トゥルーピーク : {ln['tp']:.1f} dBTP （上限 {t['tp']:.1f}）")
            print(f"  {NA} ラウドネスレンジ: {ln['lra']:.1f} LU")
        else:
            print(f"  {WARN} ラウドネスを測定できませんでした")

        sil = r["silences"]
        if sil:
            print(f"\n【無音区間】-50dB以下が0.5秒以上: {len(sil)}箇所")
            for x in sil[:5]:
                tag = {"head": " ←頭", "tail": " ←尻", "mid": ""}[x["pos"]]
                print(f"    {x['start']:7.2f}s 〜 {x['end']:7.2f}s ({x['end'] - x['start']:.2f}秒){tag}")
            if len(sil) > 5:
                print(f"    …他 {len(sil) - 5}箇所")
        else:
            print(f"\n【無音区間】{OK} なし")

    # --- 黒フレーム ---
    if r["video"]:
        black = r["blacks"]
        if black:
            print(f"\n【黒フレーム】{WARN} {len(black)}箇所")
            for x in black[:5]:
                tag = {"head": " ←頭", "tail": " ←尻", "mid": " ←途中"}[x["pos"]]
                print(f"    {x['start']:7.2f}s 〜 {x['end']:7.2f}s ({x['end'] - x['start']:.2f}秒){tag}")
            if len(black) > 5:
                print(f"    …他 {len(black) - 5}箇所")
        else:
            print(f"\n【黒フレーム】{OK} なし")

    # --- まとめ ---
    print(f"\n{'-' * 62}")
    issues = r["issues"]
    if issues:
        print(f"判定: {NG} 要確認 {len(issues)}件")
        for n, s in enumerate(issues, 1):
            print(f"  {n}. {s}")
    else:
        print(f"判定: {OK} 問題なし")
    return len(issues)


def main():
    ap = argparse.ArgumentParser(description="納品前チェック")
    ap.add_argument("files", nargs="+", help="チェックする動画ファイル")
    ap.add_argument("--target", choices=TARGETS, default="youtube", help="納品先のラウドネス基準")
    ap.add_argument("--duration", type=float, default=None, help="指定尺（秒）。ズレを判定する")
    args = ap.parse_args()

    total = 0
    for f in args.files:
        p = Path(f).expanduser()
        if not p.exists():
            print(f"{NG} ファイルが見つかりません: {p}", file=sys.stderr)
            total += 1
            continue
        try:
            total += check_file(p, args.target, args.duration)
        except Exception as e:  # noqa: BLE001 — 1ファイルの失敗で全体を止めない
            print(f"{NG} 解析に失敗: {p.name} — {e}", file=sys.stderr)
            total += 1

    print(f"\n{'=' * 62}\n合計 {len(args.files)}ファイル / 要確認 {total}件")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
