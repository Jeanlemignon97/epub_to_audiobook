from __future__ import annotations

import atexit
import html
import os
from pathlib import Path

import gradio as gr

from audiobook_generator.config.ui_config import UiConfig
from audiobook_generator.preparation.service import PreparationService
from audiobook_generator.queue.service import QueueService
from audiobook_generator.ui.preparation_view import preparation_choices, render_preparations
from audiobook_generator.ui.queue_view import render_queue, selector_choices
from audiobook_generator.ui.voice_preview import VoicePreview


OPENAI_VOICES = [
    ("Alloy — chaleureuse et équilibrée", "alloy"),
    ("Marin — naturelle et posée", "marin"),
    ("Cedar — grave et calme", "cedar"),
    ("Coral — claire et expressive", "coral"),
    ("Onyx — profonde", "onyx"),
    ("Fable — narrative", "fable"),
    ("Nova — dynamique", "nova"),
    ("Shimmer — douce", "shimmer"),
    ("Ash", "ash"), ("Ballad", "ballad"), ("Echo", "echo"),
    ("Sage", "sage"), ("Verse", "verse"),
]
EDGE_VOICES = [
    ("Guy — anglais US, agréable", "en-US-GuyNeural"),
    ("Henri — français France", "fr-FR-HenriNeural"),
    ("Denise — français France", "fr-FR-DeniseNeural"),
    ("Sylvie — français Canada", "fr-CA-SylvieNeural"),
    ("Antoine — français Canada", "fr-CA-AntoineNeural"),
    ("Jenny — anglais US", "en-US-JennyNeural"),
    ("Aria — anglais US", "en-US-AriaNeural"),
]


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
        return gr.update(choices=OPENAI_VOICES, value="alloy"), gr.update(visible=True)
    return gr.update(choices=EDGE_VOICES, value="en-US-GuyNeural"), gr.update(visible=False)


def create_ui(config: UiConfig, service: QueueService | None = None):
    data_dir = Path(os.environ.get("AUDIOBOOK_DATA_DIR", "audiobook_data"))
    service = service or QueueService(data_dir)
    preparation = PreparationService(data_dir)
    previewer = VoicePreview(service.data_dir / "previews")
    css_path = Path(__file__).with_name("styles.css")
    default_destination = os.environ.get(
        "AUDIOBOOK_OUTPUT_DIR", str(Path.home() / "Documents" / "Livres audio")
    )
    destination_label = os.environ.get("AUDIOBOOK_OUTPUT_LABEL", default_destination)
    prepared_destination = str(Path(default_destination) / "Livres EPUB")
    prepared_destination_hint = (
        f"Dans Docker, entrez un sous-dossier de {default_destination}. "
        f"Sur votre ordinateur : {destination_label}."
        if default_destination == "/exports"
        else f"Par défaut, dans {destination_label}/Livres EPUB."
    )

    def add_books(files, provider, model, voice, max_cost, destination, api_key):
        try:
            if not files:
                raise ValueError("Sélectionnez au moins un fichier EPUB.")
            ceiling = float(max_cost) if max_cost and float(max_cost) > 0 else None
            if provider == "openai":
                service.set_openai_api_key(api_key)
                preparation.set_api_key(api_key)
            jobs = service.add_books(
                files, provider=provider, model=model if provider == "openai" else "",
                voice=voice, max_cost=ceiling, destination_dir=destination,
            )
            service.start()
            message = f"✅ {len(jobs)} livre(s) ajouté(s). La conversion démarre dans l’ordre affiché."
        except Exception as exc:
            message = f"❌ {exc}"
        queue, selector, download = _refresh(service)
        return message, None, queue, selector, download

    def preview_voice(provider, model, voice, api_key):
        try:
            if provider == "openai":
                service.set_openai_api_key(api_key)
                preparation.set_api_key(api_key)
            path = previewer.generate(
                provider, model if provider == "openai" else "", voice,
                api_key=api_key,
            )
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

    def remove_duplicates(selected_id):
        try:
            count = service.remove_duplicates()
            message = (
                f"✅ {count} doublon(s) retiré(s) de la file."
                if count else "Aucun doublon en attente ou en pause."
            )
        except Exception as exc:
            message = f"❌ {exc}"
        return message, *_refresh(service, selected_id)

    def refresh_preparations(selected_id=None):
        jobs = preparation.list_jobs()
        ids = {job["id"] for job in jobs}
        selection = selected_id if selected_id in ids else (jobs[0]["id"] if jobs else None)
        selected = next((job for job in jobs if job["id"] == selection), None)
        download = (
            preparation.data_dir / "preparations" / selection / Path(selected["result_path"]).name
            if selected and selected["status"] == "completed" else None
        )
        if download and not download.is_file():
            legacy = preparation.data_dir / "preparations" / selection / "result.epub"
            download = legacy if legacy.is_file() else None
        return (
            render_preparations(jobs, default_destination, destination_label),
            gr.update(choices=preparation_choices(jobs), value=selection),
            gr.update(value=str(download) if download and download.is_file() else None,
                      visible=bool(download and download.is_file())),
        )

    def inspect_preparation(file, kind):
        try:
            if not file:
                raise ValueError("Choisissez d’abord un PDF ou un EPUB.")
            info = preparation.inspect(file, kind)
            if kind == "pdf_to_epub":
                ocr = " Reconnaissance de texte nécessaire sur certaines pages." if info["needs_ocr"] else ""
                message = f"<strong>{html.escape(info['title'])}</strong> · {info['pages']} pages.{ocr}"
            else:
                language_note = (
                    " ⚠ L’EPUB est déclaré en français ; vérifiez son contenu."
                    if info.get("language", "").lower().startswith("fr") else ""
                )
                message = (
                    f"<strong>{html.escape(info['title'])}</strong> · {info['characters']:,} caractères. "
                    f"Coût estimé : environ {info['estimated_usd']:.3f} $ US. "
                    "La facturation réelle peut varier. L’API est facturée séparément de ChatGPT."
                    + language_note
                )
            preview = html.escape(info.get("preview", "")) or "Aucun extrait disponible."
            return (
                f'<div class="analysis-result"><p>{message}</p><blockquote>{preview}</blockquote></div>',
                {"file": str(file), "kind": kind},
            )
        except Exception as exc:
            return f'<p class="job-error">{html.escape(str(exc))}</p>', None

    def add_preparation(file, kind, dest, confirmed, key, analyzed):
        try:
            if not file:
                raise ValueError("Choisissez d’abord un PDF ou un EPUB.")
            if not analyzed or analyzed.get("file") != str(file) or analyzed.get("kind") != kind:
                raise ValueError("Analysez ce livre avant de lancer sa préparation.")
            service.set_openai_api_key(key)
            job = preparation.add(file, kind, dest, confirmed=confirmed, api_key=key)
            notice = f"✅ {job['title']} ajouté à la préparation."
        except Exception as exc:
            notice = f"❌ {exc}"
        return notice, *refresh_preparations()

    def retry_preparation(job_id, key):
        try:
            if not job_id:
                raise ValueError("Choisissez une préparation dans la liste.")
            service.set_openai_api_key(key)
            preparation.retry(job_id, api_key=key)
            notice = "✅ Préparation relancée."
        except Exception as exc:
            notice = f"❌ {exc}"
        return notice, *refresh_preparations(job_id)

    with gr.Blocks(
        analytics_enabled=False,
        title="Mes livres",
        css_paths=css_path,
        fill_width=True,
    ) as ui:
        with gr.Column(elem_classes="app-shell", min_width=0):
            gr.HTML("""
            <header class="hero-copy">
              <h1>Mes livres</h1>
              <p>Préparez un EPUB français, puis écoutez-le avec une voix naturelle.</p>
            </header>
            """)
            with gr.Tabs(elem_classes="book-tabs"):
                with gr.Tab("Livre audio", id="audio"):
                    with gr.Group(elem_classes="panel"):
                        files = gr.File(
                            label="Déposer jusqu’à 3 livres EPUB",
                            file_types=[".epub"], file_count="multiple", type="filepath",
                        )
                        with gr.Row(elem_classes="settings-row"):
                            provider = gr.Dropdown(
                                [("OpenAI (recommandé)", "openai"), ("Edge (gratuit)", "edge")],
                                value="openai", label="Moteur vocal",
                            )
                            voice = gr.Dropdown(
                                OPENAI_VOICES, value="alloy", label="Voix",
                                allow_custom_value=True,
                            )
                            model = gr.Dropdown(
                                ["gpt-4o-mini-tts", "tts-1-hd", "tts-1"],
                                value="gpt-4o-mini-tts", label="Modèle",
                            )
                            max_cost = gr.Number(
                                value=0, minimum=0, label="Plafond par livre (€)",
                                info="0 désactive le plafond. Le montant affiché reste une estimation.",
                            )
                        with gr.Row(elem_classes="settings-row"):
                            destination = gr.Textbox(
                                value=default_destination,
                                label="Destination du M4B",
                                info=f"Dossier visible sur cet ordinateur : {destination_label}",
                            )
                            api_key = gr.Textbox(
                                value="", type="password", label="Clé API OpenAI (facultative)",
                                info="Gardée seulement en mémoire jusqu’au redémarrage.",
                            )
                        with gr.Row(elem_classes="action-row"):
                            preview_button = gr.Button("▶ Écouter un extrait", min_width=180)
                            add_button = gr.Button("Ajouter et convertir", variant="primary", min_width=210)
                        preview_audio = gr.Audio(label="Aperçu de la voix", interactive=False)

                    notice = gr.Markdown("Prêt à recevoir vos EPUB. Trois fichiers maximum par ajout.")
                    gr.HTML("<h2>File d’attente audio</h2>")
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
                        duplicates_button = gr.Button(
                            "Retirer les doublons en attente", elem_classes="dedupe-action"
                        )
                        download = gr.File(label="Télécharger le M4B", interactive=False, visible=False)

                with gr.Tab("Préparer un EPUB", id="prepare"):
                    gr.Markdown("Convertissez un PDF en EPUB, ou traduisez un EPUB anglais en français. Le fichier obtenu s’ouvre dans Books et peut ensuite être déposé dans l’onglet **Livre audio**.")
                    with gr.Group(elem_classes="panel"):
                        preparation_kind = gr.Radio(
                            [("PDF → EPUB", "pdf_to_epub"),
                             ("Anglais → français", "translate_en_fr")],
                            value="pdf_to_epub", label="Que voulez-vous faire ?",
                            elem_classes="preparation-choice",
                        )
                        preparation_file = gr.File(
                            label="Livre à préparer (PDF ou EPUB)",
                            file_types=[".pdf", ".epub"], type="filepath",
                        )
                        inspect_button = gr.Button("Analyser le livre")
                        analysis_html = gr.HTML("<p>Analysez le livre avant de lancer la préparation.</p>")
                        analysis_state = gr.State(None)
                        with gr.Row(elem_classes="settings-row"):
                            preparation_destination = gr.Textbox(
                                value=prepared_destination, label="Destination de l’EPUB",
                                info=prepared_destination_hint,
                            )
                            preparation_key = gr.Textbox(
                                value="", type="password", label="Clé API OpenAI pour la traduction",
                                info="Nécessaire ici ou dans .env. Gardée en mémoire seulement.",
                                visible=False,
                            )
                        confirm_translation = gr.Checkbox(
                            value=False,
                            label="J’ai lu l’estimation et j’accepte la facturation API de la traduction.",
                            elem_classes="translation-confirmation",
                            visible=False,
                        )
                        prepare_button = gr.Button("Lancer la préparation", variant="primary")
                    preparation_notice = gr.Markdown("Un livre est préparé à la fois. Actualiser la page ne l’arrête pas.")
                    gr.HTML("<h2>Livres préparés</h2>")
                    preparation_queue = gr.HTML(render_preparations(
                        preparation.list_jobs(), default_destination, destination_label
                    ))
                    with gr.Group(elem_classes="panel"):
                        preparation_selected = gr.Dropdown(
                            choices=preparation_choices(preparation.list_jobs()),
                            label="Livre sélectionné",
                        )
                        preparation_retry = gr.Button("Réessayer")
                        preparation_download = gr.File(
                            label="Télécharger l’EPUB", interactive=False, visible=False,
                        )
                    gr.Markdown("Les PDF complexes peuvent perdre leur mise en page et leurs images. Les pages scannées nécessitent Tesseract. Vérifiez quelques pages dans Books avant de lancer l’audio.")

            gr.HTML("""
            <footer class="app-footer">
              <p>Un livre est traité à la fois dans chaque file. Les résultats restent dans la destination choisie.</p>
            </footer>
            """)

        provider.change(_provider_options, provider, [voice, model])
        preview_button.click(
            preview_voice, [provider, model, voice, api_key], [preview_audio, notice]
        )
        add_button.click(
            add_books,
            [files, provider, model, voice, max_cost, destination, api_key],
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
        duplicates_button.click(
            remove_duplicates, selected_job,
            [notice, queue_html, selected_job, download],
        )
        preparation_kind.change(
            lambda kind: (gr.update(value=False, visible=kind == "translate_en_fr"),
                          "<p>Analysez le livre avant de lancer la préparation.</p>", None,
                          gr.update(visible=kind == "translate_en_fr"),
                          gr.update(value=None)),
            preparation_kind, [confirm_translation, analysis_html, analysis_state,
                               preparation_key, preparation_file],
        )
        preparation_file.change(
            lambda: ("<p>Analysez le livre avant de lancer la préparation.</p>",
                     gr.update(value=False), None),
            outputs=[analysis_html, confirm_translation, analysis_state],
        )
        inspect_button.click(
            inspect_preparation, [preparation_file, preparation_kind],
            [analysis_html, analysis_state],
        )
        prepare_button.click(
            add_preparation,
            [preparation_file, preparation_kind, preparation_destination,
             confirm_translation, preparation_key, analysis_state],
            [preparation_notice, preparation_queue, preparation_selected,
             preparation_download],
        )
        preparation_retry.click(
            retry_preparation, [preparation_selected, preparation_key],
            [preparation_notice, preparation_queue, preparation_selected,
             preparation_download],
        )
        preparation_selected.change(
            lambda job_id: refresh_preparations(job_id)[2],
            preparation_selected, preparation_download,
        )
        timer = gr.Timer(2.0)
        timer.tick(lambda job_id: _refresh(service, job_id), selected_job, [queue_html, selected_job, download])
        timer.tick(
            lambda job_id: refresh_preparations(job_id), preparation_selected,
            [preparation_queue, preparation_selected, preparation_download],
        )

    ui.queue(default_concurrency_limit=4)
    ui.queue_service = service
    ui.preparation_service = preparation
    atexit.register(service.close)
    atexit.register(preparation.close)
    return ui


def host_ui(config: UiConfig):
    ui = create_ui(config)
    output_dir = Path(
        os.environ.get(
            "AUDIOBOOK_OUTPUT_DIR", str(Path.home() / "Documents" / "Livres audio")
        )
    )
    ui.launch(
        server_name=config.host,
        server_port=config.port,
        allowed_paths=[str(ui.queue_service.data_dir.resolve()), str(output_dir.resolve())],
    )
