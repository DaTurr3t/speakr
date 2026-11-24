"""PDF transcript download: ?format=pdf on /download/transcript, and raw=1.

The PDF is rendered by src.services.document (fpdf2 with the DejaVu Sans fonts
in static/fonts): render_turns_to_pdf for diarized transcripts, laid out as
speaker turns, render_text_to_pdf for plain text. The text in a PDF is stored as
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
from src.services.document import render_text_to_pdf, render_turns_to_pdf
from src.utils.transcript_render import speaker_turns

app.config["WTF_CSRF_ENABLED"] = False

SEGMENTS = json.dumps([
    {"speaker": "Alice", "sentence": "Grüezi mitenand.", "start_time": 0.0, "end_time": 1.0},
    {"speaker": "Alice", "sentence": "Wie geht's?", "start_time": 1.0, "end_time": 2.0},
    {"sentence": "No speaker here.", "start_time": 2.0, "end_time": 3.0},
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


def _written(render, *args, with_style=False):
    """The pdf bytes and the texts render hands to multi_cell (with the font
    style and text color when with_style)."""
    written = []
    original = FPDF.multi_cell

    def recording_multi_cell(self, w, h, text="", *a, **kwargs):
        written.append((text, self.font_style, self.text_color.serialize().strip()) if with_style else text)
        return original(self, w, h, text, *a, **kwargs)

    with patch.object(FPDF, "multi_cell", recording_multi_cell):
        pdf = render(*args)
    return pdf, written


def _written_text(title, body):
    return _written(render_text_to_pdf, title, body)


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
# Speaker turns
# --------------------------------------------------------------------------- #

TURN_SEGMENTS = [
    {"speaker": "Alice", "sentence": "One.", "start_time": 0.0, "end_time": 1.0},
    {"speaker": "Alice", "sentence": "Two.", "start_time": 1.0, "end_time": 2.0},
    {"speaker": "Bob", "sentence": "Three.", "start_time": 2.0, "end_time": 3.0},
    {"speaker": "Alice", "sentence": "Four.", "start_time": 3.0, "end_time": 4.0},
]


def test_speaker_turns_group_consecutive_segments_of_a_speaker():
    assert speaker_turns(TURN_SEGMENTS, "[{{speaker}}]: {{text}}") == [
        ("Alice", ["One.", "Two."]), ("Bob", ["Three."]), ("Alice", ["Four."]),
    ]


def test_speaker_turns_drop_only_the_speaker_part_of_the_template():
    turns = speaker_turns(TURN_SEGMENTS[:3], "{{index}}. [{{start_time}}] {{speaker|upper}} - {{text}}")
    assert turns == [("Alice", ["1. [00:00:00] One.", "2. [00:00:01] Two."]), ("Bob", ["3. [00:00:02] Three."])]
    assert speaker_turns(TURN_SEGMENTS[:1], "{{speaker}}: {{text}}") == [("Alice", ["One."])]
    assert speaker_turns(TURN_SEGMENTS[:1], "{{text}}") == [("Alice", ["One."])]


def test_render_turns_to_pdf_labels_each_speaker_change():
    turns = [("Alice", ["One.", "Two."]), ("Bob", ["Three."]), ("Alice", ["Four."])]
    pdf, written = _written(render_turns_to_pdf, "Meeting", turns, "Header line", with_style=True)
    assert pdf.startswith(b"%PDF-")
    texts = [text for text, _, _ in written]
    assert texts == ["Meeting", "Header line", "Alice", "One.\nTwo.", "Bob", "Three.", "Alice", "Four."]
    labels = {text: (style, color) for text, style, color in written if text in ("Alice", "Bob")}
    # Labels are bold and colored, one color per speaker, the lines are plain black
    assert labels["Alice"][0] == "B" and labels["Bob"][0] == "B"
    assert labels["Alice"][1] != labels["Bob"][1]
    alice_colors = {color for text, _, color in written if text == "Alice"}
    assert len(alice_colors) == 1
    lines = [(style, color) for text, style, color in written if text in ("One.\nTwo.", "Three.")]
    assert all(style == "" for style, _ in lines)
    assert lines[0][1] == lines[1][1] and lines[0][1] not in (labels["Alice"][1], labels["Bob"][1])


def test_render_turns_to_pdf_without_header_and_with_long_names():
    turns = [("", ["x"]), ("A" * 300, ["y " * 10000])]
    pdf, written = _written(render_turns_to_pdf, "", turns, None)
    assert written[:3] == ["Transcript", "Unknown", "x"]
    assert len(re.findall(rb"/Type /Page\b", pdf)) > 1


# --------------------------------------------------------------------------- #
# Download endpoint
# --------------------------------------------------------------------------- #

def test_pdf_download_of_a_diarized_transcript():
    with app.app_context():
        user = _mk_user()
        rec = _mk_recording(user, "Weekly Sync", SEGMENTS)
        with patch("src.api.recordings.render_turns_to_pdf", wraps=render_turns_to_pdf) as render:
            resp = _client(user).get(f"/recording/{rec.id}/download/transcript?format=pdf")
        assert resp.status_code == 200
        assert resp.headers["Content-Type"] == "application/pdf"
        assert resp.headers["Content-Disposition"] == 'attachment; filename="Weekly Sync_formatted.pdf"'
        assert resp.data.startswith(b"%PDF-")
        # Without a template: the basic format minus its "[speaker]: " part,
        # one label per speaker change
        render.assert_called_once_with("Weekly Sync", [
            ("Alice", ["Grüezi mitenand.", "Wie geht's?"]),
            ("Unknown", ["No speaker here."]),
        ], None)


def test_pdf_download_uses_the_chosen_template():
    with app.app_context():
        user = _mk_user()
        tmpl = TranscriptTemplate(user_id=user.id, name="Minutes", header="Protokoll",
                                  template="[{{start_time}}] {{speaker|upper}} - {{text}}")
        db.session.add(tmpl)
        db.session.commit()
        rec = _mk_recording(user, "Board", SEGMENTS)
        with patch("src.api.recordings.render_turns_to_pdf", return_value=b"%PDF-stub") as render:
            resp = _client(user).get(f"/recording/{rec.id}/download/transcript?format=pdf&template_id={tmpl.id}")
        assert resp.status_code == 200
        assert resp.headers["Content-Disposition"] == 'attachment; filename="Board_Minutes.pdf"'
        render.assert_called_once_with("Board", [
            ("Alice", ["[00:00:00] Grüezi mitenand.", "[00:00:01] Wie geht's?"]),
            ("Unknown", ["[00:00:02] No speaker here."]),
        ], "Protokoll")


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
        with patch("src.api.recordings.render_turns_to_pdf", return_value=b"%PDF-stub") as render:
            resp = client.get(f"/recording/{rec.id}/download/transcript?format=pdf&raw=1")
        assert resp.headers["Content-Disposition"] == 'attachment; filename="Raw_raw.pdf"'
        render.assert_called_once_with("Raw", [
            ("Alice", ["Grüezi mitenand.", "Wie geht's?"]),
            ("Unknown", ["No speaker here."]),
        ], None)
        # raw=1 is not limited to the PDF
        resp = client.get(f"/recording/{rec.id}/download/transcript?raw=1")
        assert resp.get_data(as_text=True) == (
            "Alice: Grüezi mitenand.\nAlice: Wie geht's?\nUnknown: No speaker here.")
        assert resp.headers["Content-Disposition"] == 'attachment; filename="Raw_raw.txt"'


def test_pdf_download_of_someone_elses_recording_is_refused():
    with app.app_context():
        owner, other = _mk_user(), _mk_user()
        rec = _mk_recording(owner, "Private", SEGMENTS)
        with patch("src.api.recordings.render_turns_to_pdf") as render:
            resp = _client(other).get(f"/recording/{rec.id}/download/transcript?format=pdf")
        assert resp.status_code == 404
        render.assert_not_called()
