from __future__ import annotations

import atexit
import os
from pathlib import Path

import gradio as gr

from audiobook_generator.config.ui_config import UiConfig
from audiobook_generator.queue.service import QueueService
from audiobook_generator.ui.queue_view import render_queue, selector_choices
from audiobook_generator.ui.voice_preview import VoicePreview


OPENAI_VOICES = ["coral", "marin", "cedar", "nova", "shimmer", "fable", "onyx"]
EDGE_VOICES = ["fr-FR-DeniseNeural", "fr-FR-HenriNeural", "fr-CA-SylvieNeural", "fr-CA-AntoineNeural"]


def _refresh(service: QueueService, selected_id=None):
    jobs = service.list_jobs()
    ids = {job.id for job in jobs}
    selection = selected_id if selected_id in ids else (jobs[0].id if jobs else None)
    selected = next((job for job in jobs if job.id == selection), None)
    download = selected.final_path if selected and selected.final_path else None
    return (
        render_queue(jobs),
        gr.update(choices=selector_choices(jobs), value=selection),
        gr.update(value=download, visible=bool(download)),
    )


def _provider_options(provider: str):
    if provider == "openai":
        return gr.update(choices=OPENAI_VOICES, value="coral"), gr.update(visible=True)
    return gr.update(choices=EDGE_VOICES, value="fr-FR-DeniseNeural"), gr.update(visible=False)


def create_ui(config: UiConfig, service: QueueService | None = None):
    data_dir = Path(os.environ.get("AUDIOBOOK_DATA_DIR", "audiobook_data"))
    service = service or QueueService(data_dir)
    previewer = VoicePreview(service.data_dir / "previews")
    css_path = Path(__file__).with_name("styles.css")

    def add_books(files, provider, model, voice, max_cost):
        try:
            if not files:
                raise ValueError("Sélectionnez au moins un fichier EPUB.")
            ceiling = float(max_cost) if max_cost and float(max_cost) > 0 else None
            jobs = service.add_books(
                files, provider=provider, model=model if provider == "openai" else "",
                voice=voice, max_cost=ceiling,
            )
            service.start()
            message = f"✅ {len(jobs)} livre(s) ajouté(s). La conversion démarre dans l’ordre affiché."
        except Exception as exc:
            message = f"❌ {exc}"
        queue, selector, download = _refresh(service)
        return message, None, queue, selector, download

    def preview_voice(provider, model, voice):
        try:
            path = previewer.generate(provider, model if provider == "openai" else "", voice)
            return str(path), "✅ Extrait prêt. Écoutez-le avant de lancer vos livres."
        except Exception as exc:
            return None, f"❌ {exc}"

    def act(action, selected_id):
        if not selected_id:
            return "Sélectionnez un livre dans la file.", *_refresh(service, selected_id)
        try:
            if action == "pause":
                service.pause(selected_id)
                message = "⏸️ Mise en pause demandée."
            elif action == "resume":
                service.resume(selected_id)
                message = "▶️ Conversion remise dans la file."
            elif action == "retry":
                service.retry(selected_id)
                message = "↻ Nouvel essai lancé avec les chapitres déjà terminés."
            else:
                service.delete(selected_id)
                message = "Livre retiré de la file."
        except Exception as exc:
            message = f"❌ {exc}"
        return message, *_refresh(service, selected_id)

    with gr.Blocks(
        analytics_enabled=False,
        title="Mes livres audio",
        css_paths=css_path,
        fill_width=True,
    ) as ui:
        with gr.Column(elem_classes="app-shell"):
            gr.HTML("""
            <header class="hero-copy">
              <h1>Convertir mes EPUB</h1>
              <p>Transformez vos livres en un fichier M4B, avec une voix naturelle.</p>
            </header>
            """)
            with gr.Group(elem_classes="panel"):
                files = gr.File(
                    label="Déposer jusqu’à 3 livres EPUB",
                    file_types=[".epub"], file_count="multiple", type="filepath",
                )
                with gr.Row():
                    provider = gr.Dropdown(
                        [("OpenAI (recommandé)", "openai"), ("Edge (gratuit)", "edge")],
                        value="openai", label="Moteur vocal",
                    )
                    voice = gr.Dropdown(OPENAI_VOICES, value="coral", label="Voix")
                    model = gr.Dropdown(
                        ["gpt-4o-mini-tts", "tts-1-hd", "tts-1"],
                        value="gpt-4o-mini-tts", label="Modèle",
                    )
                    max_cost = gr.Number(
                        value=0, minimum=0, label="Plafond par livre (€)",
                        info="0 désactive le plafond. Le montant affiché reste une estimation.",
                    )
                with gr.Row(elem_classes="action-row"):
                    preview_button = gr.Button("▶ Écouter un extrait", min_width=180)
                    add_button = gr.Button("Ajouter et convertir", variant="primary", min_width=210)
                preview_audio = gr.Audio(label="Aperçu de la voix", interactive=False)

            notice = gr.Markdown("Prêt à recevoir vos EPUB. Trois fichiers maximum par ajout.")
            gr.HTML("<h2>File d’attente</h2>")
            queue_html = gr.HTML(render_queue(service.list_jobs()))

            with gr.Group(elem_classes="panel"):
                selected_job = gr.Dropdown(
                    choices=selector_choices(service.list_jobs()), label="Livre sélectionné"
                )
                with gr.Row(elem_classes="action-row"):
                    pause_button = gr.Button("Mettre en pause")
                    resume_button = gr.Button("Reprendre")
                    retry_button = gr.Button("Réessayer")
                    delete_button = gr.Button("Retirer")
                download = gr.File(label="Télécharger le M4B", interactive=False, visible=False)

            gr.HTML("""
            <footer class="app-footer">
              <p>Un livre est traité à la fois. Les chapitres terminés sont conservés en cas d’arrêt.</p>
            </footer>
            """)

        provider.change(_provider_options, provider, [voice, model])
        preview_button.click(preview_voice, [provider, model, voice], [preview_audio, notice])
        add_button.click(
            add_books, [files, provider, model, voice, max_cost],
            [notice, files, queue_html, selected_job, download],
        )
        pause_button.click(
            lambda job_id: act("pause", job_id), selected_job,
            [notice, queue_html, selected_job, download],
        )
        resume_button.click(
            lambda job_id: act("resume", job_id), selected_job,
            [notice, queue_html, selected_job, download],
        )
        retry_button.click(
            lambda job_id: act("retry", job_id), selected_job,
            [notice, queue_html, selected_job, download],
        )
        delete_button.click(
            lambda job_id: act("delete", job_id), selected_job,
            [notice, queue_html, selected_job, download],
        )
        timer = gr.Timer(2.0)
        timer.tick(lambda job_id: _refresh(service, job_id), selected_job, [queue_html, selected_job, download])

    ui.queue(default_concurrency_limit=4)
    ui.queue_service = service
    atexit.register(service.close)
    return ui


def host_ui(config: UiConfig):
    ui = create_ui(config)
    ui.launch(
        server_name=config.host,
        server_port=config.port,
        allowed_paths=[str(ui.queue_service.data_dir)],
    )
