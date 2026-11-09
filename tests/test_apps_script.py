"""Apps Script의 흐름과 형식 검증. 실제 Google 환경에서의 동작은 배포 후에 따로 확인한다."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "apps_script_harness.js"
SCRIPT = ROOT / "apps-script" / "Code.gs"
MANIFEST = ROOT / "apps-script" / "appsscript.json"


def _run_harness() -> list[dict]:
    done = subprocess.run(
        ["osascript", "-l", "JavaScript", str(HARNESS), str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.skipif(not shutil.which("osascript"), reason="macOS 전용")
def test_apps_script_scenarios():
    results = _run_harness()
    failures = [f"{item['name']}: {item.get('error')}" for item in results if not item["ok"]]
    assert failures == []
    assert len(results) >= 20


def test_manifest_requests_only_what_is_needed():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert set(manifest["oauthScopes"]) == {
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/script.external_request",
        "https://www.googleapis.com/auth/script.scriptapp",
    }
    services = {s["userSymbol"]: s["version"] for s in manifest["dependencies"]["enabledAdvancedServices"]}
    assert services == {"Gmail": "v1", "Drive": "v3"}
    assert manifest["timeZone"] == "Asia/Seoul"
    assert manifest["runtimeVersion"] == "V8"


def test_script_never_modifies_mail():
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("Messages.trash", "Messages.delete", "Messages.modify", "GmailApp", "MailApp"):
        assert forbidden not in source


def test_script_contains_no_secrets():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "LEDGER_HMAC_KEY" in source  # 이름만 있고 값은 스크립트 속성에 둔다
    assert "vercel.app" not in source


def test_gate_signature_format_matches_script():
    """Code.gs는 '타임스탬프.경로.본문'을 HMAC-SHA256으로 서명해 16진수로 보낸다."""
    import hashlib
    import hmac

    from app.auth.gate import sign_body

    body = b'{"script_version":"1"}'
    expected = hmac.new(b"key", b"1700000000./api/ingest/heartbeat." + body, hashlib.sha256).hexdigest()
    assert sign_body("key", "1700000000", "/api/ingest/heartbeat", body) == expected
