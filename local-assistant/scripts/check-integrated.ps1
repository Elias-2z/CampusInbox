param([switch]$Browser)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath (Join-Path $PSScriptRoot '..')
. (Join-Path $PSScriptRoot 'find-python.ps1')
$python=Resolve-CIPython
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
& $python -B -m unittest discover -s tests -q
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File desktop-app\run-tests.ps1
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
foreach($script in @('lint:check','format:check','test','build')){
    & npm.cmd --prefix frontend run $script
    if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
}
if($Browser){
    $runtimeModules=Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
    $localModules=Join-Path (Get-Location).Path 'desktop-sync\node_modules'
    if(Test-Path -LiteralPath $runtimeModules){$env:NODE_PATH=$runtimeModules}else{$env:NODE_PATH=$localModules}
    & node scripts/test-integrated-ui.cjs
    if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
    & node scripts/test-unified-task-import.cjs
    if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
    & node scripts/test-integrated-dialogs.cjs
    if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
    & node scripts/test-integrated-samples.cjs
    if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
}
