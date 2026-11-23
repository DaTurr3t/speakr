"""PDF transcript download: ?format=pdf on /download/transcript, and raw=1.

The PDF is rendered by src.services.document.render_text_to_pdf (fpdf2 with
the DejaVu Sans fonts in static/fonts). The text in a PDF is stored as
subset glyph ids, so the tests read what the renderer was asked to write
rather than parse the file.

SHARED-DB: every assertion is scoped to the user/recordings the test created.
"""

import json
import os
import re
import sys
import uuid
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpdf import FPDF

from src.app import app, db
from src.models import Recording, User
from src.models.templates import TranscriptTemplate
from src.services import document
from src.services.document import render_text_to_pdf

app.config["WTF_CSRF_ENABLED"] = False

SEGMENTS = json.dumps([
    {"speaker": "Alice", "sentence": "Grüezi mitenand.", "start_time": 0.0, "end_time": 1.0},
    {"sentence": "No speaker here.", "start_time": 1.0, "end_time": 2.0},
], ensure_ascii=False)


def _mk_user():
    suffix = uuid.uuid4().hex[:8]
    user = User(username=f"pdf_{suffix}", email=f"pdf_{suffix}@local.test", password="x")
    db.session.add(user)
    db.session.commit()
    return user


def _client(user):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
    return client


def _mk_recording(user, title, transcription):
    rec = Recording(user_id=user.id, title=title, status="COMPLETED",
                    audio_path="local://recordings/x.mp3", original_filename="x.mp3",
                    transcription=transcription)
    db.session.add(rec)
    db.session.commit()
    return rec


def _written_text(title, body):
    """The pdf bytes and the texts render_text_to_pdf hands to multi_cell."""
    written = []
    original = FPDF.multi_cell

    def recording_multi_cell(self, w, h, text="", *args, **kwargs):
        written.append(text)
        return original(self, w, h, text, *args, **kwargs)

    with patch.object(FPDF, "multi_cell", recording_multi_cell):
        pdf = render_text_to_pdf(title, body)
    return pdf, written


# --------------------------------------------------------------------------- #
# Renderer
# --------------------------------------------------------------------------- #

def test_render_text_to_pdf_writes_title_then_body():
    pdf, written = _written_text("Sitzung über Änderungen", "Größe: ä ö ü ß é\nПривет")
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
    assert written == ["Sitzung über Änderungen", "Größe: ä ö ü ß é\nПривет"]
    # The fonts are embedded, so the umlauts do not depend on the reader
    assert b"DejaVuSans" in pdf


def test_render_text_to_pdf_normalizes_line_endings_tabs_and_control_chars():
    _, written = _written_text("  T\x07itle  ", "a\r\nb\rc\td\x00e\x1bf")
    assert written == ["Title", "a\nb\nc    def"]


def test_render_text_to_pdf_empty_title_and_body():
    pdf, written = _written_text("", None)
    assert written == ["Transcript", ""]
    assert pdf.startswith(b"%PDF-")


def test_render_text_to_pdf_breaks_long_text_across_pages():
    body = "\n".join(f"Speaker {i}: " + "word " * 40 for i in range(300))
    body += "\n" + "x" * 5000  # one word wider than the page is broken, not refused
    pdf = render_text_to_pdf("Long", body)
    assert len(re.findall(rb"/Type /Page\b", pdf)) > 10


def test_fonts_ship_with_their_license():
    for name in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "LICENSE"):
        assert (document._PDF_FONT_DIR / name).is_file(), name


# --------------------------------------------------------------------------- #
# Download endpoint
# --------------------------------------------------------------------------- #

def test_pdf_download_of_a_diarized_transcript():
    with app.app_context():
        user = _mk_user()
        rec = _mk_recording(user, "Weekly Sync", SEGMENTS)
        with patch("src.api.recordings.render_text_to_pdf", wraps=render_text_to_pdf) as render:
            resp = _client(user).get(f"/recording/{rec.id}/download/transcript?format=pdf")
        assert resp.status_code == 200
        assert resp.headers["Content-Type"] == "application/pdf"
        assert resp.headers["Content-Disposition"] == 'attachment; filename="Weekly Sync_formatted.pdf"'
        assert resp.data.startswith(b"%PDF-")
        # The same templated body as the TXT download, under the title
        txt = _client(user).get(f"/recording/{rec.id}/download/transcript?format=txt").get_data(as_text=True)
        render.assert_called_once_with("Weekly Sync", txt)


def test_pdf_download_uses_the_chosen_template():
    with app.app_context():
        user = _mk_user()
        tmpl = TranscriptTemplate(user_id=user.id, name="Minutes", template="{{speaker|upper}} - {{text}}")
        db.session.add(tmpl)
        db.session.commit()
        rec = _mk_recording(user, "Board", SEGMENTS)
        with patch("src.api.recordings.render_text_to_pdf", return_value=b"%PDF-stub") as render:
            resp = _client(user).get(f"/recording/{rec.id}/download/transcript?format=pdf&template_id={tmpl.id}")
        assert resp.status_code == 200
        assert resp.headers["Content-Disposition"] == 'attachment; filename="Board_Minutes.pdf"'
        title, body = render.call_args.args
        assert title == "Board"
        assert body.splitlines()[0] == "ALICE - Grüezi mitenand."


def test_pdf_download_of_a_plain_text_transcript_and_untitled_recording():
    with app.app_context():
        user = _mk_user()
        rec = _mk_recording(user, None, "Just some words.")
        with patch("src.api.recordings.render_text_to_pdf", return_value=b"%PDF-stub") as render:
            resp = _client(user).get(f"/recording/{rec.id}/download/transcript?format=PDF")
        assert resp.status_code == 200
        assert resp.headers["Content-Disposition"] == 'attachment; filename="transcript.pdf"'
        render.assert_called_once_with("Transcript", "Just some words.")


def test_pdf_download_with_a_non_ascii_title():
    with app.app_context():
        user = _mk_user()
        rec = _mk_recording(user, "Besprechung Größe", "text")
        resp = _client(user).get(f"/recording/{rec.id}/download/transcript?format=pdf")
        assert resp.status_code == 200
        assert resp.headers["Content-Disposition"] == (
            f'attachment; filename="transcript-{rec.id}.pdf"; '
            "filename*=UTF-8''Besprechung%20Gr%C3%B6%C3%9Fe.pdf")


def test_raw_download_writes_speaker_and_text_without_a_template():
    with app.app_context():
        user = _mk_user()
        tmpl = TranscriptTemplate(user_id=user.id, name="Default", template="X {{text}}", is_default=True)
        db.session.add(tmpl)
        db.session.commit()
        rec = _mk_recording(user, "Raw", SEGMENTS)
        client = _client(user)
        with patch("src.api.recordings.render_text_to_pdf", return_value=b"%PDF-stub") as render:
            resp = client.get(f"/recording/{rec.id}/download/transcript?format=pdf&raw=1")
        assert resp.headers["Content-Disposition"] == 'attachment; filename="Raw_raw.pdf"'
        render.assert_called_once_with("Raw", "Alice: Grüezi mitenand.\nUnknown: No speaker here.")
        # raw=1 is not limited to the PDF
        resp = client.get(f"/recording/{rec.id}/download/transcript?raw=1")
        assert resp.get_data(as_text=True) == "Alice: Grüezi mitenand.\nUnknown: No speaker here."
        assert resp.headers["Content-Disposition"] == 'attachment; filename="Raw_raw.txt"'


def test_pdf_download_of_someone_elses_recording_is_refused():
    with app.app_context():
        owner, other = _mk_user(), _mk_user()
        rec = _mk_recording(owner, "Private", SEGMENTS)
        with patch("src.api.recordings.render_text_to_pdf") as render:
            resp = _client(other).get(f"/recording/{rec.id}/download/transcript?format=pdf")
        assert resp.status_code == 404
        render.assert_not_called()
