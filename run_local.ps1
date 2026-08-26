# 毎朝このPCで新着を取得して push する。タスクスケジューラから呼ばれる。
#
# GitHub Actions のスケジュールは高負荷時に実行ごと破棄されることがあるため、
# こちらを冗長系として併走させる。どちらが先に走っても結果は壊れない:
#   - 新着判定は「その日に初めて見たか」なので、同じ日に二重に走っても
#     先に見つけた分は新着のまま残り、二重計上にもならない
#   - push 前に必ず rebase して、もう一方が先に押した分を取り込む
#
# 実行ログは logs\ に日付別で残る。失敗しても次の日には自動で再挑戦される。

$ErrorActionPreference = "Stop"

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

Log "=== start ==="

try {
    # 1) 先にリモートへ追随する。Actions 側が push 済みならその内容から続ける
    git -C $repo fetch origin 2>&1 | ForEach-Object { Log "fetch: $_" }
    git -C $repo reset --hard origin/main 2>&1 | ForEach-Object { Log "reset: $_" }

    # 2) 取得
    Log "running fetch_news.py"
    $out = & $python (Join-Path $repo "scripts\fetch_news.py") 2>&1
    $out | ForEach-Object { Log "py: $_" }
    if ($LASTEXITCODE -ne 0) { throw "fetch_news.py exited with $LASTEXITCODE" }

    # 3) 差分があれば push。無ければ何もしない
    $changed = git -C $repo status --porcelain -- docs/data/news.json data/seen.json
    if (-not $changed) {
        Log "no changes"
    } else {
        git -C $repo add docs/data/news.json data/seen.json
        $msg = "Update news " + (Get-Date -Format "yyyy-MM-dd HH:mm") + " JST (local)"
        git -C $repo commit -m $msg 2>&1 | ForEach-Object { Log "commit: $_" }

        # push 直前にもう一度追随する。取得中に Actions が押している場合がある
        git -C $repo pull --rebase origin main 2>&1 | ForEach-Object { Log "rebase: $_" }
        git -C $repo push origin main 2>&1 | ForEach-Object { Log "push: $_" }
        if ($LASTEXITCODE -ne 0) { throw "git push failed with $LASTEXITCODE" }
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
