from __future__ import annotations

from html import escape
from pathlib import Path

STATUS = {
    "pending": ("En attente", "status-waiting"),
    "running": ("En cours", "status-running"),
    "paused": ("Interrompu", "status-paused"),
    "failed": ("Erreur", "status-error"),
    "completed": ("Prêt", "status-done"),
}


def render_preparations(
    jobs: list[dict], container_root: str = "", visible_root: str = ""
) -> str:
    if not jobs:
        return '<div class="empty-queue"><div class="empty-icon">◇</div><h2>Aucun livre préparé</h2><p>Importez un PDF ou un EPUB anglais pour commencer.</p></div>'
    cards = []
    for job in jobs:
        label, css = STATUS.get(job["status"], ("Inconnu", "status-info"))
        action = "Traduction anglaise → française" if job["kind"] == "translate_en_fr" else "PDF → EPUB"
        fraction = max(0, min(100, round(job["progress"] * 100)))
        path = Path(job["result_path"])
        if container_root and visible_root:
            try:
                path = Path(visible_root) / path.relative_to(Path(container_root))
            except ValueError:
                pass
        result = (
            f'<p class="ready-note">EPUB : {escape(str(path))}</p>'
            if job["status"] == "completed" else ""
        )
        error = f'<p class="job-error">{escape(job["error"])}</p>' if job["error"] else ""
        cards.append(f'''
            <article class="job-card preparation-card">
              <div class="book-mark" aria-hidden="true">▤</div>
              <div class="job-identity"><h3>{escape(job["title"])}</h3><p>{escape(action)}</p></div>
              <div class="job-progress">
                <div class="status-line"><span class="status-badge {css}">{label}</span><span>{fraction} %</span></div>
                <div class="progress-track" role="progressbar" aria-valuenow="{fraction}" aria-valuemin="0" aria-valuemax="100"><span style="width:{fraction}%"></span></div>
                {result}{error}
              </div>
            </article>''')
    return '<div class="queue-list">' + "".join(cards) + '</div>'


def preparation_choices(jobs: list[dict]) -> list[tuple[str, str]]:
    return [(f"{job['title']} — {STATUS.get(job['status'], ('Inconnu',))[0]}", job["id"]) for job in jobs]
