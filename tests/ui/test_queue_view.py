from audiobook_generator.queue.models import BookAnalysis, BookJob, JobStatus
from audiobook_generator.ui.queue_view import render_queue


def job(status: JobStatus, error="", final_path=""):
    return BookJob(
        id="abc", source_path="source.epub", output_dir="chapters",
        original_name="livre.epub", provider="openai", model="gpt-4o-mini-tts",
        voice="coral", analysis=BookAnalysis("Titre très long sans troncature", "Auteur", 10, 50_000, 3.5, 1.25),
        status=status, progress=0.4, completed_chapters=4, error=error,
        final_path=final_path,
    )


def test_empty_queue_has_clear_call_to_action():
    html = render_queue([])
    assert "Aucun livre" in html
    assert "Déposez" in html


def test_queue_card_contains_primary_values_and_textual_status():
    html = render_queue([job(JobStatus.CONVERTING)])
    assert "Titre très long sans troncature" in html
    assert "Conversion en cours" in html
    assert "4 / 10 chapitres" in html
    assert "1,25 €" in html
    assert "40 %" in html
    assert "Temps restant estimé" in html
    assert "≈ 6 min" in html


def test_pending_job_displays_total_conversion_estimate():
    html = render_queue([job(JobStatus.PENDING)])
    assert "Conversion estimée" in html
    assert "≈ 10 min" in html


def test_error_and_completed_states_are_explicit():
    html = render_queue([
        job(JobStatus.FAILED, error="Clé API absente"),
        job(JobStatus.COMPLETED, final_path="/tmp/livre.m4b"),
    ])
    assert "Clé API absente" in html
    assert "Erreur" in html
    assert "Terminé" in html
    assert "M4B prêt" in html


def test_user_content_is_html_escaped():
    unsafe = job(JobStatus.PENDING)
    unsafe = BookJob(**{**unsafe.__dict__, "analysis": BookAnalysis("<script>", "A&B", 1, 1, 1, 1)})
    html = render_queue([unsafe])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "A&amp;B" in html
