import subprocess
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class IntegratedLauncherTests(unittest.TestCase):
    def test_start_script_parses_in_windows_powershell(self):
        code="$tokens=$null;$errors=$null;[System.Management.Automation.Language.Parser]::ParseFile((Join-Path (Get-Location) 'start.ps1'),[ref]$tokens,[ref]$errors)|Out-Null;if($errors.Count){$errors|ForEach-Object Message;exit 1}"
        result=subprocess.run(['powershell.exe','-NoProfile','-Command',code],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_copy_uses_distinct_default_service_ports(self):
        self.assertIn('[int]$Port=8877',(ROOT/'start.ps1').read_text(encoding='utf-8-sig'))
        self.assertIn('CourseInboxLearningAssistantUiIntegration', (ROOT/'desktop-app/Program.cs').read_text(encoding='utf-8-sig'))
        self.assertIn('port = 8877;',(ROOT/'desktop-app/Program.cs').read_text(encoding='utf-8-sig'))
        self.assertIn("default=8878",(ROOT/'server.py').read_text(encoding='utf-8'))
        self.assertIn("default=8879",(ROOT/'server.py').read_text(encoding='utf-8'))
