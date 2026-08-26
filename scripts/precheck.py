#!/usr/bin/env python3
"""書き出し前チェック — Premiere .prproj のタイムライン構造を解析して編集事故を検出する。

.prproj は gzip 圧縮された XML。シーケンスのトラック構造とクリップ配置をフレーム精度で読み、
書き出す前に気づきたい事故（黒パカ・カット点の1〜2Fズレ・無効化クリップの取り残し）を
シーケンスタイムコード付きのリストで返す。

使い方:
    python3 precheck.py <prproj> --list                 # シーケンス一覧（ドロップレットの選択肢用）
    python3 precheck.py <prproj> --sequence <番号>       # 解析して HTML+TXT を prproj の隣に保存
    python3 precheck.py <prproj> --gui                  # tkinter で選択（Windows用）
    オプション: --no-open（ブラウザで開かない・テスト用）

注意: .prproj の中身は Adobe の非公開仕様。Premiere のバージョンによって構造が変わる可能性がある。
検証済み: PremiereData Version=3 / Sequence ClassID 6a15d903-…（2026-08 実案件で確認）
"""

import gzip
import sys
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime
from html import escape
from pathlib import Path

TPS = 254016000000  # Premiere のティック数/秒（固定値）

NEAR_MISS_MAX = 2   # カット点ニアミスとみなす最大フレーム差
BLACK_GAP_MAX = 2   # 「黒パカ疑い」とみなす最大フレーム数（それ超は「空白」として情報表示）

MULTICAM_WORDS = ("マルチカメラ", "Multicam", "multicam", "オートリフレーム")

# 黒パカ判定で「画面を覆っている」と数えないクリップ（下が透けるもの）
NON_OPAQUE_WORDS = ("調整レイヤー", "Adjustment Layer", "adjustment layer")


# ---------------------------------------------------------------- 解析コア

class Project:
    def __init__(self, path):
        self.path = Path(path)
        try:
            raw = gzip.decompress(self.path.read_bytes())
        except OSError:  # 非圧縮の prproj（稀にある）
            raw = self.path.read_bytes()
        self.root = ET.fromstring(raw)
        self.by_id, self.by_uid = {}, {}
        for el in self.root.iter():
            if el.get("ObjectID"):
                self.by_id[el.get("ObjectID")] = el
            if el.get("ObjectUID"):
                self.by_uid[el.get("ObjectUID")] = el

    def deref(self, el):
        if el is None:
            return None
        r = el.get("ObjectRef")
        u = el.get("ObjectURef")
        return self.by_id.get(r) if r else (self.by_uid.get(u) if u else None)

    # --- シーケンス一覧 ---

    def sequences(self):
        out = []
        for s in self.root.iter("Sequence"):
            if not s.get("ObjectUID"):
                continue
            tg = s.find("TrackGroups")
            if tg is None:
                continue
            name = s.findtext("Name") or "(名称不明)"
            info = {"el": s, "name": name, "multicam": any(w in name for w in MULTICAM_WORDS)}
            try:
                v_inner = self._group_inner(s, 0)
                info["tpf"] = int(v_inner.findtext("FrameRate"))
                info["fps"] = TPS / info["tpf"]
            except Exception:
                continue
            v_tracks = self._tracks(s, 0)
            a_tracks = self._tracks(s, 1)
            info["n_v"] = sum(1 for t in v_tracks if t)
            info["n_a"] = sum(1 for t in a_tracks if t)
            end_ticks = max(
                [c["end_ticks"] for t in v_tracks + a_tracks for c in t] or [0])
            info["dur_sec"] = end_ticks / TPS
            out.append(info)
        # マルチカメラ・オートリフレーム系は末尾へ
        out.sort(key=lambda i: i["multicam"])
        return out

    def _group_inner(self, seq, idx):
        g = self.deref(seq.find("TrackGroups").findall("TrackGroup")[idx].find("Second"))
        return g.find("TrackGroup")

    def _tracks(self, seq, idx):
        """トラックごとのクリップリスト。ticks のまま返す。"""
        try:
            inner = self._group_inner(seq, idx)
        except Exception:
            return []
        if inner is None or inner.find("Tracks") is None:
            return []
        tracks = []
        for tref in inner.find("Tracks").findall("Track"):
            tr = self.deref(tref)
            items = []
            ct = tr.find(".//ClipItems/TrackItems") if tr is not None else None
            if ct is not None:
                for ref in ct.findall("TrackItem"):
                    item = self.deref(ref)
                    if item is None:
                        continue
                    cti = item.find("ClipTrackItem")
                    if cti is None:
                        continue
                    ti = cti.find("TrackItem")
                    sub = self.deref(cti.find("SubClip"))
                    clip = self.deref(sub.find("Clip")) if sub is not None else None
                    items.append({
                        "start_ticks": int(ti.findtext("Start") or 0),
                        "end_ticks": int(ti.findtext("End") or 0),
                        "name": (sub.findtext("Name") if sub is not None else None) or "(名称不明)",
                        "disabled": (clip is not None and clip.findtext("Disabled") == "true"),
                    })
            tracks.append(items)
        return tracks


# ---------------------------------------------------------------- タイムコード

def tc(frame, fps):
    """フレーム番号 → シーケンスタイムコード。29.97/59.94はドロップフレーム表記。"""
    frame = int(round(frame))
    nominal = round(fps)
    drop = nominal in (30, 60) and abs(fps - nominal) > 0.01
    if drop:
        dpm = 2 * (nominal // 30)          # 1分あたりのドロップ数（29.97=2, 59.94=4）
        fpm = nominal * 60 - dpm
        fp10 = nominal * 600 - dpm * 9
        d, m = divmod(frame, fp10)
        if m > dpm:
            frame += dpm * 9 * d + dpm * ((m - dpm) // fpm)
        else:
            frame += dpm * 9 * d
        sep = ";"
    else:
        sep = ":"
    ff = frame % nominal
    ss = (frame // nominal) % 60
    mm = (frame // (nominal * 60)) % 60
    hh = frame // (nominal * 3600)
    return f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"


# ---------------------------------------------------------------- 検出

def analyze(prj, seq_info):
    seq = seq_info["el"]
    tpf = seq_info["tpf"]
    fps = seq_info["fps"]

    def frames(track_list):
        return [
            {**c, "s": round(c["start_ticks"] / tpf), "e": round(c["end_ticks"] / tpf)}
            for c in track_list
        ]

    v_tracks = [frames(t) for t in prj._tracks(seq, 0)]

    findings = []          # (frame, 種別, 詳細)  要確認
    infos = []             # (frame, 種別, 詳細)  情報

    # --- 1) 全ビデオトラック合成の隙間（黒パカ・空白） ---
    def opaque(c):
        return not any(w in c["name"] for w in NON_OPAQUE_WORDS)

    ivs = sorted((c["s"], c["e"]) for t in v_tracks for c in t
                 if not c["disabled"] and opaque(c))
    merged = []
    for s, e in ivs:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    content_end = merged[-1][1] if merged else 0
    for a, b in zip(merged, merged[1:]):
        gap = b[0] - a[1]
        if gap <= BLACK_GAP_MAX:
            findings.append((a[1], "黒パカ疑い",
                             f"全ビデオトラックに {gap}フレーム の隙間（映像が黒く抜ける）"))
        else:
            infos.append((a[1], "空白",
                          f"{gap}フレーム（{gap / fps:.1f}秒）の空白。意図した黒 or 本編の区切りか確認"))

    # --- 2) 同一トラック内の1〜2F隙間（下のトラックで隠れていても編集ミスの可能性） ---
    hole_set = {(a[1], b[0]) for a, b in zip(merged, merged[1:])}
    for ti, track in enumerate(v_tracks, start=1):
        srt = sorted((c for c in track if not c["disabled"]), key=lambda c: c["s"])
        for a, b in zip(srt, srt[1:]):
            gap = b["s"] - a["e"]
            if 0 < gap <= BLACK_GAP_MAX and (a["e"], b["s"]) not in hole_set:
                infos.append((a["e"], "トラック内の隙間",
                              f"V{ti} に {gap}フレーム の隙間（他トラックで隠れて見えないが、"
                              f"『{a['name'][:20]}』→『{b['name'][:20]}』の間にズレの可能性）"))

    # --- 3) カット点ニアミス（トラック間で±1〜2Fずれた編集点） ---
    edges = []
    for ti, track in enumerate(v_tracks, start=1):
        per = {}
        for c in track:
            if c["disabled"]:
                continue
            per.setdefault(c["s"], []).append(("開始", c["name"]))
            per.setdefault(c["e"], []).append(("終了", c["name"]))
        edges.append(per)
    seen = set()
    for i in range(len(edges)):
        for j in range(len(edges)):
            if i == j or not edges[i] or not edges[j]:
                continue
            other = sorted(edges[j])
            for f, marks in edges[i].items():
                best = min(other, key=lambda x: abs(x - f))
                d = f - best
                if 0 < abs(d) <= NEAR_MISS_MAX:
                    key = (min(f, best), max(f, best), min(i, j), max(i, j))
                    if key in seen:
                        continue
                    seen.add(key)
                    kind, name = marks[0]
                    findings.append((f, "カット点ズレ",
                                     f"V{i + 1}『{name[:24]}』の{kind}が V{j + 1} の編集点"
                                     f"（{tc(best, fps)}）から {d:+d}フレーム"))

    # --- 4) 無効化クリップの取り残し ---
    for ti, track in enumerate(v_tracks, start=1):
        for c in track:
            if c["disabled"]:
                infos.append((c["s"], "無効化クリップ",
                              f"V{ti}『{c['name'][:24]}』が無効化されたまま残っている"
                              f"（〜{tc(c['e'], fps)}）"))

    findings.sort(key=lambda x: x[0])
    infos.sort(key=lambda x: x[0])
    return {
        "seq": seq_info,
        "fps": fps,
        "content_end": content_end,
        "findings": findings,
        "infos": infos,
    }


# ---------------------------------------------------------------- 出力

def build_txt(result, prproj_path):
    s = result["seq"]
    fps = result["fps"]
    L = []
    L.append("=" * 64)
    L.append("書き出し前チェック（タイムライン構造）")
    L.append(f"プロジェクト : {Path(prproj_path).name}")
    L.append(f"シーケンス   : {s['name']}")
    L.append(f"設定         : {fps:.2f}fps / V{s['n_v']} A{s['n_a']} / "
             f"内容の終端 {tc(result['content_end'], fps)}")
    L.append(f"実行         : {datetime.now():%Y-%m-%d %H:%M}")
    L.append("=" * 64)
    L.append("")
    L.append(f"【要確認】{len(result['findings'])}件")
    if result["findings"]:
        for f, kind, detail in result["findings"]:
            L.append(f"  {tc(f, fps)}  [{kind}] {detail}")
    else:
        L.append("  なし ✓")
    L.append("")
    L.append(f"【情報・意図確認】{len(result['infos'])}件")
    if result["infos"]:
        for f, kind, detail in result["infos"]:
            L.append(f"  {tc(f, fps)}  [{kind}] {detail}")
    else:
        L.append("  なし")
    L.append("")
    L.append("-" * 64)
    L.append("※ タイムラインの構造から検出できる項目のみ。ラウドネス・尺などの実測は")
    L.append("  書き出し後のファイルを納品チェックにドロップして行う。")
    return "\n".join(L)


CSS = """
body { font-family: 'Hiragino Sans', 'Yu Gothic UI', Meiryo, sans-serif;
       background: #f4f5f7; color: #1a1d21; margin: 0; padding: 32px 16px; }
.wrap { max-width: 860px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; }
.meta { color: #667; font-size: 13px; margin-bottom: 20px; }
.summary { padding: 14px 18px; border-radius: 10px; font-size: 15px; font-weight: 600; margin-bottom: 24px; }
.summary.ok { background: #e6f4ea; color: #1e7e34; }
.summary.ng { background: #fdecea; color: #c0392b; }
h2 { font-size: 15px; color: #445; margin: 26px 0 8px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; background: #fff;
        border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #eef0f2; vertical-align: top; }
th { color: #667; font-weight: 600; background: #fafbfc; }
td.tc { font-family: ui-monospace, Menlo, Consolas, monospace; white-space: nowrap; font-weight: 700; }
td.kind { white-space: nowrap; }
.ng td.kind { color: #c0392b; }
.info td.kind { color: #667; }
.empty { color: #1e7e34; font-weight: 600; padding: 10px 4px; }
footer { color: #aab; font-size: 11px; text-align: center; margin-top: 28px; }
"""


def build_html(result, prproj_path, txt_path):
    s = result["seq"]
    fps = result["fps"]
    now = datetime.now()
    n = len(result["findings"])
    cls, verdict = ("ok", "✓ 構造上の問題は見つかりませんでした") if n == 0 else ("ng", f"✗ 要確認 {n}件")

    def rows(items, row_cls):
        if not items:
            return '<div class="empty">なし ✓</div>'
        r = "".join(
            f'<tr class="{row_cls}"><td class="tc">{tc(f, fps)}</td>'
            f'<td class="kind">{escape(kind)}</td><td>{escape(detail)}</td></tr>'
            for f, kind, detail in items)
        return f'<table><tr><th>タイムコード</th><th>種別</th><th>内容</th></tr>{r}</table>'

    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<title>書き出し前チェック {escape(s['name'])}</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>書き出し前チェック</h1>
<div class="meta">{now:%Y-%m-%d %H:%M}　{escape(Path(prproj_path).name)} ／
シーケンス「{escape(s['name'])}」　{fps:.2f}fps / V{s['n_v']} A{s['n_a']} /
内容の終端 {tc(result['content_end'], fps)}</div>
<div class="summary {cls}">{verdict}</div>
<h2>要確認（{len(result['findings'])}件）</h2>
{rows(result['findings'], 'ng')}
<h2>情報・意図確認（{len(result['infos'])}件）</h2>
{rows(result['infos'], 'info')}
<footer>テキスト版: {escape(str(txt_path))}<br>
タイムライン構造から検出できる項目のみ。ラウドネス・尺などの実測は書き出し後のファイルで（納品チェック）。</footer>
</div></body></html>"""


def run_analysis(prproj_path, seq_info, prj, open_browser=True):
    result = analyze(prj, seq_info)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    safe = "".join(ch for ch in seq_info["name"] if ch not in '\\/:*?"<>|')[:30]
    base = Path(prproj_path).expanduser().resolve().parent
    try:
        txt_path = base / f"{stamp}_書き出し前チェック_{safe}.txt"
        txt_path.write_text(build_txt(result, prproj_path), encoding="utf-8")
    except OSError:
        base = Path.home()
        txt_path = base / f"{stamp}_書き出し前チェック_{safe}.txt"
        txt_path.write_text(build_txt(result, prproj_path), encoding="utf-8")
    html_path = base / f"{stamp}_書き出し前チェック_{safe}.html"
    html_path.write_text(build_html(result, prproj_path, txt_path), encoding="utf-8")
    if open_browser:
        webbrowser.open(html_path.as_uri())
    return result, html_path, txt_path


# ---------------------------------------------------------------- CLI / GUI

def fmt_dur(sec):
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def seq_label(i, info):
    tag = "〔素材系〕" if info["multicam"] else ""
    return (f"{i}) {tag}{info['name']} — {fmt_dur(info['dur_sec'])} / "
            f"{info['fps']:.2f}fps / V{info['n_v']} A{info['n_a']}")


def run_gui(prproj_path, prj, seqs):
    """Windows用: tkinterでシーケンスを選んで解析。"""
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("書き出し前チェック — シーケンス選択")
    frm = ttk.Frame(root, padding=14)
    frm.grid()
    ttk.Label(frm, text=Path(prproj_path).name, font=("", 12, "bold")).grid(row=0, column=0, sticky="w")
    lb = tk.Listbox(frm, width=64, height=min(18, len(seqs)))
    for i, info in enumerate(seqs):
        lb.insert("end", seq_label(i, info))
    lb.selection_set(0)
    lb.grid(row=1, column=0, pady=8)
    status = ttk.Label(frm, text="")
    status.grid(row=2, column=0, sticky="w")

    def go():
        sel = lb.curselection()
        if not sel:
            return
        status.config(text="解析中…")
        root.update()
        try:
            _, html_path, _ = run_analysis(prproj_path, seqs[sel[0]], prj)
            status.config(text=f"完了: {html_path.name}")
            root.destroy()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("書き出し前チェック", f"解析に失敗しました:\n{e}")

    ttk.Button(frm, text="このシーケンスをチェック", command=go).grid(row=3, column=0, pady=(8, 0))
    root.mainloop()
    return 0


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    mode_list = "--list" in argv
    mode_gui = "--gui" in argv
    no_open = "--no-open" in argv
    seq_sel = None
    files = []
    it = iter(argv)
    for a in it:
        if a == "--sequence":
            seq_sel = next(it)
        elif a not in ("--list", "--gui", "--no-open"):
            files.append(a)
    if not files:
        print("prprojファイルを指定してください", file=sys.stderr)
        return 2

    prproj = files[0]
    prj = Project(prproj)
    seqs = prj.sequences()
    if not seqs:
        print("シーケンスが見つかりませんでした", file=sys.stderr)
        return 1

    if mode_list:
        for i, info in enumerate(seqs):
            print(seq_label(i, info))
        return 0

    if mode_gui:
        return run_gui(prproj, prj, seqs)

    if seq_sel is None:
        print("--list / --gui / --sequence <番号> のいずれかを指定してください", file=sys.stderr)
        return 2
    info = seqs[int(seq_sel)]
    result, html_path, txt_path = run_analysis(prproj, info, prj, open_browser=not no_open)
    print(f"要確認 {len(result['findings'])}件 / 情報 {len(result['infos'])}件")
    print(f"レポート: {html_path}")
    print(f"テキスト: {txt_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
