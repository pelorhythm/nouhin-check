-- 納品チェック ドロップレット（Mac用）
-- ビルド: bash make_mac_droplet.sh（デスクトップに 納品チェック.app を作る）
-- 動画ファイルをドロップ → 納品先を選んで実測チェック（書き出し後）
-- .prproj をドロップ → シーケンスを選んでタイムライン構造チェック（書き出し前）
-- ※ GUIはmacOS純正ダイアログを使う（システムPythonのtkinterは描画されないため使わない）

on scriptsDir()
	-- アプリの隣（リポジトリ内 mac/ 配置 → ../scripts、ルート配置 → ./scripts）を優先し、
	-- 見つからなければ従来の ~/.claude/skills/nouhin-check/scripts/ を使う
	set appPosix to POSIX path of (path to me)
	repeat with rel in {"../scripts/", "../../scripts/", "scripts/"}
		set cand to appPosix & rel
		try
			do shell script "test -f " & quoted form of (cand & "precheck.py")
			return cand
		end try
	end repeat
	return (POSIX path of (path to home folder)) & ".claude/skills/nouhin-check/scripts/"
end scriptsDir

on pyRun(scriptName, args)
	return do shell script "PATH=/opt/homebrew/bin:/usr/local/bin:$PATH /usr/bin/python3 " & quoted form of (scriptsDir() & scriptName) & " " & args
end pyRun

on open theFiles
	set prprojPath to missing value
	set mediaArgs to ""
	set mediaCount to 0
	repeat with f in theFiles
		set p to POSIX path of f
		if p ends with ".prproj" then
			if prprojPath is missing value then set prprojPath to p
		else
			set mediaArgs to mediaArgs & " " & quoted form of p
			set mediaCount to mediaCount + 1
		end if
	end repeat

	if prprojPath is not missing value then
		if mediaCount > 0 then
			display notification "prproj優先で処理します（動画は別途ドロップしてください）" with title "納品チェック"
		end if
		my handlePrproj(prprojPath)
	else
		my handleMedia(mediaArgs)
	end if
end open

-- ============================ 書き出し前チェック（.prproj）

on handlePrproj(p)
	try
		set listing to my pyRun("precheck.py", quoted form of p & " --list")
	on error errMsg
		display dialog "プロジェクトを読み込めませんでした:" & return & errMsg buttons {"OK"} default button 1 with icon stop with title "書き出し前チェック"
		return
	end try
	set choices to paragraphs of listing
	set sel to choose from list choices with prompt "チェックするシーケンスを選んでください" default items {item 1 of choices} with title "書き出し前チェック"
	if sel is false then return
	set selText to item 1 of sel
	set idx to word 1 of selText
	display notification "解析中…" with title "書き出し前チェック"
	try
		my pyRun("precheck.py", quoted form of p & " --sequence " & idx & " 2>&1")
	on error errMsg
		display dialog "解析に失敗しました:" & return & errMsg buttons {"OK"} default button 1 with icon stop with title "書き出し前チェック"
	end try
end handlePrproj

-- ============================ 納品チェック（書き出し後の実測）

on handleMedia(args)
	if args is "" then
		display dialog "書き出した動画ファイル、またはPremiereの .prproj をこのアイコンにドロップしてください。" buttons {"OK"} default button 1 with title "納品チェック"
		return
	end if

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

	display notification "測定中…（ファイル数・尺によって時間がかかります）" with title "納品チェック"
	try
		my pyRun("nouhin_gui.py", args & " --no-gui --target " & targetKey & durArg & " 2>&1")
	on error errMsg
		display dialog "チェックに失敗しました:" & return & errMsg buttons {"OK"} default button 1 with icon stop with title "納品チェック"
	end try
end handleMedia

on run
	my handleMedia("")
end run
