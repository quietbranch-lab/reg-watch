# 毎朝このPCで新着を取得して push する。タスクスケジューラから呼ばれる。
#
# GitHub Actions のスケジュールは高負荷時に実行ごと破棄されることがあるため、
# こちらを冗長系として併走させる。どちらが先に走っても結果は壊れない:
#   - 新着判定は「その日に初めて見たか」なので、同じ日に二重に走っても
#     先に見つけた分は新着のまま残り、二重計上にもならない
#   - push 前に必ず rebase して、もう一方が先に押した分を取り込む
#
# 【重要1】このファイルは必ず BOM 付き UTF-8 で保存すること。
# Windows PowerShell 5.1 は BOM の無い .ps1 を Shift-JIS として読むため、
# 日本語コメントが化けて直後の1行を飲み込む。実際にそれで
# `$changed = git status ...` の行が消え、毎回「no changes」と判定されて
# push されない状態が2日続いた。
#
# 【重要2】git は正常時でも進捗を標準エラーに書く（"From https://..." 等）。
# $ErrorActionPreference = "Stop" のまま 2>&1 すると、成功しているのに
# 例外として中断してしまう。成否は必ず終了コードで判定すること。
#
# 【重要3】下の reset --hard はこのファイル自身もリポジトリの内容に戻す。
# ローカルで編集しただけでは次回実行時に消えるので、必ずコミットすること。
#
# 実行ログは logs\ に日付別で残る。失敗しても次の日には自動で再挑戦される。

$ErrorActionPreference = "Continue"

$repo   = "C:\dev\reg-watch"
$python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$gitDir = "C:\Program Files\Git\cmd"
$ghDir  = "C:\Program Files\GitHub CLI"

$env:Path = "$ghDir;$gitDir;" + $env:Path
$env:GIT_TERMINAL_PROMPT = "0"

$logDir = Join-Path $repo "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("run-" + (Get-Date -Format "yyyy-MM-dd") + ".log")

function Log($msg) {
    $line = (Get-Date -Format "HH:mm:ss") + "  " + $msg
    Write-Output $line
    Add-Content -Path $log -Value $line -Encoding utf8
}

# git を実行し、出力をログに流して終了コードで成否を判定する
function Invoke-Git {
    param([string]$Tag, [Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $output = & git -C $repo @GitArgs 2>&1
    $code = $LASTEXITCODE
    foreach ($line in $output) { Log "${Tag}: $line" }
    if ($code -ne 0) { throw "git $($GitArgs -join ' ') failed with exit code $code" }
}

Log "=== start ==="

try {
    # 先にリモートへ追随する。Actions 側が push 済みならその内容から続ける
    Log "step: fetch + reset"
    Invoke-Git -Tag "fetch" fetch origin
    Invoke-Git -Tag "reset" reset --hard origin/main

    # 取得
    Log "step: fetch_news.py"
    $out = & $python (Join-Path $repo "scripts\fetch_news.py") 2>&1
    $pyCode = $LASTEXITCODE
    foreach ($line in $out) { Log "py: $line" }
    if ($pyCode -ne 0) { throw "fetch_news.py exited with $pyCode" }

    # 要約を生成する。付加機能なので、失敗しても一覧の更新は止めない
    Log "step: summarize.py"
    $sum = & $python (Join-Path $repo "scripts\summarize.py") 2>&1
    foreach ($line in $sum) { Log "sum: $line" }
    if ($LASTEXITCODE -ne 0) { Log "WARN: 要約の生成に失敗しましたが続行します" }

    # 差分があれば push。無ければ何もしない
    Log "step: check diff"
    $changed = & git -C $repo status --porcelain -- docs/data/news.json data/seen.json data/summaries.json
    Log ("diff: " + $(if ($changed) { ($changed -join " / ") } else { "(なし)" }))

    if (-not $changed) {
        Log "no changes"
    } else {
        Invoke-Git -Tag "add" add docs/data/news.json data/seen.json data/summaries.json
        $msg = "Update news " + (Get-Date -Format "yyyy-MM-dd HH:mm") + " JST (local)"
        Invoke-Git -Tag "commit" commit -m $msg

        # push 直前にもう一度追随する。取得中に Actions が先に押している場合がある
        Log "step: rebase + push"
        Invoke-Git -Tag "rebase" pull --rebase origin main
        Invoke-Git -Tag "push" push origin main
        Log "pushed: $msg"
    }

    Log "=== done ==="
    exit 0
}
catch {
    Log "ERROR: $($_.Exception.Message)"
    Log "=== failed ==="
    exit 1
}
