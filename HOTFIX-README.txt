WardrobeShare Local AI readiness regression test fix

Issue: Once a real models/u2netp.onnx file exists, prepush gate used to fail test_required_local_ai_model_fails_readiness_before_production, because the test assumed the file was absent.

Fix: In the test, monkeypatch MODEL_FILE to a guaranteed missing path inside a disposable temporary directory; still require HTTP 503 and ready=false when REQUIRE_BACKGROUND_AI=True. Production code is unchanged.

From inside wardrobeshare-web:
  cp tests/test_integration.py tests/test_integration.py.bak
  unzip -o ~/Downloads/WardrobeShare-Local-AI-Gate-Test-Hotfix-20261118.zip -d .
  bash scripts/prepush_gate.sh

Do not delete your models folder or your .env.
