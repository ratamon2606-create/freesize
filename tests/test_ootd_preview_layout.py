"""Guard against OOTD modal images escaping their positioning container."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_outfit_modal_has_a_positioned_clipped_preview():
    js = (ROOT / "static/js/app.js").read_text()
    css = (ROOT / "static/css/app.css").read_text()
    assert 'class="outfit-detail-grid"' in js
    assert 'class="outfit-detail-preview"' in js
    assert '.outfit-detail-preview .preview {' in css
    rule = css.split('.outfit-detail-preview .preview {', 1)[1].split('}', 1)[0]
    assert 'position: relative' in rule
    assert 'overflow: hidden' in rule
    assert 'isolation: isolate' in rule
    assert '@media (max-width: 650px)' in css

def test_outfit_gallery_preview_is_unchanged():
    css = (ROOT / "static/css/app.css").read_text()
    assert '.outfit-card .preview{' in css
