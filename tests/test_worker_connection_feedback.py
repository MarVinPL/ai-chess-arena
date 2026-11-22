from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / 'remote_worker_gui/public/index.html').read_text()

def test_connection_feedback_next_to_button():
    assert HTML.index('id="pair"') < HTML.index('id="connectionFeedback"') < HTML.index('Token musi zostać')

def test_connection_feedback_accessible_and_explicit():
    assert 'id="connectionFeedback" class="muted" role="status" aria-live="polite"' in HTML
    assert "button.disabled=true;" in HTML
    assert "button.disabled=false;" in HTML
    assert "has_saved_worker_token" in HTML
    assert 'Sprawdzanie połączenia HTTPS i tokenu' in HTML
