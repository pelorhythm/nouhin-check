-- 納品チェック ドロップレット（Mac用）
-- ビルド: bash make_mac_droplet.sh（デスクトップに 納品チェック.app を作る）
-- 動画ファイルをアイコンにドロップ → 納品先を選ぶ → nouhin_gui.py --no-gui が測定してHTMLレポートを開く
-- ※ GUIはmacOS純正ダイアログを使う（システムPythonのtkinterは描画されないため使わない）

on scriptPath()
	return (POSIX path of (path to home folder)) & ".claude/skills/nouhin-check/scripts/nouhin_gui.py"
end scriptPath

on open theFiles
	-- 納品先を選ぶ
	set choices to {"YouTube / SNS（-14 LUFS）", "TV放送・CM納品（-24 LKFS / ARIB）", "Web広告 汎用（-16 LUFS）"}
	set sel to choose from list choices with prompt "納品先を選んでください" default items {item 1 of choices} with title "納品チェック"
	if sel is false then return
	set selText to item 1 of sel
	set targetKey to "youtube"
	if selText starts with "TV" then set targetKey to "broadcast"
	if selText starts with "Web" then set targetKey to "web"

	-- 指定尺（任意）
	set d to text returned of (display dialog "指定尺（秒・任意）" & return & "CM・広告枠など尺が決まっている時だけ入力（例: 30）。不要なら空のままOK。" default answer "" with title "納品チェック")
	set durArg to ""
	if d is not "" then
		try
			set _check to d as number
		on error
			display dialog "指定尺は数字（秒）で入力してください。例: 30" buttons {"OK"} default button 1 with icon caution with title "納品チェック"
			return
		end try
		set durArg to " --duration " & quoted form of d
	end if

	set args to ""
	repeat with f in theFiles
		set args to args & " " & quoted form of POSIX path of f
	end repeat

	display notification "測定中…（ファイル数・尺によって時間がかかります）" with title "納品チェック"
	try
		do shell script "PATH=/opt/homebrew/bin:/usr/local/bin:$PATH /usr/bin/python3 " & quoted form of scriptPath() & args & " --no-gui --target " & targetKey & durArg & " 2>&1"
	on error errMsg
		display dialog "チェックに失敗しました:" & return & errMsg buttons {"OK"} default button 1 with icon stop with title "納品チェック"
	end try
end open

on run
	display dialog "書き出した動画ファイルを、このアイコンにドロップしてください。" buttons {"OK"} default button 1 with title "納品チェック"
end run
