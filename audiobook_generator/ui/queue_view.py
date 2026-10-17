from __future__ import annotations

from html import escape

from audiobook_generator.queue.models import BookJob, JobStatus


STATUS_LABELS = {
    JobStatus.ANALYZING: ("Analyse", "status-info"),
    JobStatus.PENDING: ("En attente", "status-waiting"),
    JobStatus.CONVERTING: ("Conversion en cours", "status-running"),
    JobStatus.PAUSED: ("En pause", "status-paused"),
    JobStatus.FAILED: ("Erreur", "status-error"),
    JobStatus.COMPLETED: ("Terminé", "status-done"),
}


def _number(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def estimated_conversion_minutes(job: BookJob) -> int:
    speed_factor = 12 if job.provider == "edge" else 20
    total = max(1, round(job.analysis.estimated_hours * 60 / speed_factor))
    if job.status is JobStatus.CONVERTING:
        return max(1, round(total * (1 - job.progress)))
    return total


def render_queue(jobs: list[BookJob]) -> str:
    if not jobs:
        return """
        <section class="empty-queue" aria-live="polite">
          <span class="empty-icon" aria-hidden="true">◇</span>
          <h2>Aucun livre dans la file</h2>
          <p>Déposez vos EPUB ci-dessus pour préparer vos premiers livres audio.</p>
        </section>
        """
    cards = []
    for job in jobs:
        label, status_class = STATUS_LABELS[job.status]
        percent = max(0, min(100, round(job.progress * 100)))
        error = (
            f'<p class="job-error" role="alert">{escape(job.error)}</p>'
            if job.error else ""
        )
        ready = '<span class="ready-note">M4B prêt</span>' if job.final_path else ""
        eta_label = (
            "Temps restant estimé"
            if job.status is JobStatus.CONVERTING
            else "Conversion estimée"
        )
        eta = estimated_conversion_minutes(job)
        cards.append(f"""
        <article class="job-card" data-status="{job.status.value}">
          <div class="book-mark" aria-hidden="true">▤</div>
          <div class="job-identity">
            <h3>{escape(job.analysis.title)}</h3>
            <p>{escape(job.analysis.author)}</p>
            <p class="job-source">{escape(job.original_name)}</p>
          </div>
          <div class="job-progress">
            <div class="status-line">
              <span class="status-badge {status_class}">{label}</span>{ready}
              <strong>{percent} %</strong>
            </div>
            <div class="progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{percent}">
              <span style="width:{percent}%"></span>
            </div>
            <div class="job-metrics">
              <span>{job.completed_chapters} / {job.analysis.chapter_count} chapitres</span>
              <span>≈ {_number(job.analysis.estimated_hours)} h</span>
              <span>≈ {_number(job.analysis.estimated_cost)} €</span>
              <span>{eta_label} : ≈ {eta} min</span>
            </div>
            {error}
          </div>
        </article>
        """)
    return '<section class="queue-list" aria-live="polite">' + "".join(cards) + "</section>"


def selector_choices(jobs: list[BookJob]):
    return [(f"{job.analysis.title} — {STATUS_LABELS[job.status][0]}", job.id) for job in jobs]
