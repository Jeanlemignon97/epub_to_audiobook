# EPUB Audiobook Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a local responsive web app that queues multiple EPUB files, previews a natural voice, resumes interrupted conversions, and exports one chapterized M4B per book.

**Architecture:** Keep the existing parser and provider interfaces, add a SQLite-backed queue and a single background runner, and invoke the existing conversion engine through an isolated subprocess. Cache chapter files atomically and package them with FFmpeg only after all chapters succeed.

**Tech Stack:** Python 3.11, Gradio 5, SQLite, OpenAI/Edge/Azure/Piper providers, FFmpeg/ffprobe, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-12-epub-audiobook-queue-design.md`

## Global Constraints

- Only DRM-free `.epub` files are accepted.
- The V1 does not translate books and does not synchronize displayed text with audio.
- Provider secrets remain environment variables and are never persisted.
- One `.m4b` is produced per book; original EPUB chapters remain internal markers.
- Completed non-empty chapter audio is reused on resume.
- UI breakpoints are compact `<600`, medium `600-839`, expanded `840-1199`, and wide `>=1200` logical pixels.
- Primary text and actions may not be clipped or unexpectedly ellipsized.

---

### Task 1: Persistent jobs and EPUB analysis

**Files:**
- Create: `audiobook_generator/queue/__init__.py`
- Create: `audiobook_generator/queue/models.py`
- Create: `audiobook_generator/queue/store.py`
- Create: `audiobook_generator/queue/analyzer.py`
- Test: `tests/queue/test_store.py`
- Test: `tests/queue/test_analyzer.py`

**Interfaces:**
- Produces: `JobStatus`, `BookJob`, `JobStore`, and `analyze_epub(path, provider_config) -> BookAnalysis`.

- [ ] Write tests proving SQLite CRUD, ordered pending selection, allowed state transitions, and restart persistence.
- [ ] Run `python -m pytest tests/queue/test_store.py -q` and confirm the missing modules fail.
- [ ] Implement immutable dataclasses, ISO timestamps, parameterized SQL, and transaction-protected transitions.
- [ ] Write analysis tests using `examples/The_Life_and_Adventures_of_Robinson_Crusoe.epub` and an invalid file.
- [ ] Implement parsing, metadata extraction, non-empty chapter counting, character total, estimated hours at 900 characters/minute, and provider cost delegation.
- [ ] Run both queue test modules and commit with `feat: add persistent audiobook jobs`.

### Task 2: Atomic chapter resume and progress

**Files:**
- Modify: `audiobook_generator/core/audiobook_generator.py`
- Create: `audiobook_generator/core/progress.py`
- Test: `tests/test_audiobook_generator.py`

**Interfaces:**
- Produces: `ProgressEvent(chapter_index, chapter_count, state, path)` and optional `progress_callback`/`cancel_event` constructor arguments on `AudiobookGenerator`.

- [ ] Write tests showing a non-empty completed chapter is skipped, an empty chapter is regenerated, output is atomically renamed, and cancellation stops before the next chapter.
- [ ] Run the focused tests and confirm they fail for the new behaviors.
- [ ] Generate to `<output>.part`, validate non-zero size, replace the final path atomically, and emit progress events.
- [ ] Preserve legacy CLI behavior when no callback or cancellation event is supplied.
- [ ] Run legacy and new generator tests and commit with `feat: resume completed audiobook chapters`.

### Task 3: Chapterized M4B packaging

**Files:**
- Create: `audiobook_generator/core/m4b_packager.py`
- Test: `tests/test_m4b_packager.py`
- Modify: `Dockerfile`

**Interfaces:**
- Produces: `M4BPackager.package(tracks, destination, title, author) -> Path` and `PackagingError`.

- [ ] Write tests for natural filename ordering, ffprobe duration parsing, escaped FFmetadata, chapter boundary accumulation, missing FFmpeg, and failed command retention of MP3 files.
- [ ] Run the tests and confirm the implementation is absent.
- [ ] Implement argument-list subprocess calls without shell interpolation, temporary concat/metadata files, AAC encoding, and atomic destination replacement.
- [ ] Confirm the Docker image installs both FFmpeg and ffprobe from the same Debian package.
- [ ] Run packaging tests and commit with `feat: package chapters as m4b`.

### Task 4: Queue runner, pause, retry, and cost guardrail

**Files:**
- Create: `audiobook_generator/queue/runner.py`
- Create: `audiobook_generator/queue/service.py`
- Test: `tests/queue/test_runner.py`
- Test: `tests/queue/test_service.py`

**Interfaces:**
- Consumes: `JobStore`, `AudiobookGenerator`, and `M4BPackager`.
- Produces: `QueueService.add_books`, `start`, `pause`, `resume`, `retry`, `delete`, `list_jobs`, and `close`.

- [ ] Write tests with fake subprocess/packager objects for FIFO success, error, retry, pause, restart recovery, and a rejected job above its optional cost ceiling.
- [ ] Run focused tests and confirm they fail.
- [ ] Implement per-job working directories, safe copied filenames, process-group termination, state transitions, progress derived from cached chapter files, and sequential execution.
- [ ] Ensure deleting a running job is rejected and secrets are taken only from the runner environment.
- [ ] Run queue tests and commit with `feat: orchestrate audiobook conversion queue`.

### Task 5: Responsive French web interface and voice preview

**Files:**
- Replace: `audiobook_generator/ui/web_ui.py`
- Create: `audiobook_generator/ui/queue_view.py`
- Create: `audiobook_generator/ui/styles.css`
- Create: `audiobook_generator/ui/voice_preview.py`
- Modify: `main_ui.py`
- Test: `tests/ui/test_queue_view.py`
- Test: `tests/ui/test_voice_preview.py`
- Create: `tests/ui/test_responsive.py`

**Interfaces:**
- Consumes: `QueueService`.
- Produces: `host_ui(config)` and a Gradio app with multi-file import, voice controls, preview audio, cost ceiling, queue actions, status refresh, and M4B downloads.

- [ ] Write view-model tests for empty, waiting, converting, error, paused, and completed cards with French labels and non-truncated primary values.
- [ ] Write voice preview tests proving the sample is length-limited, uses the selected provider, and surfaces a missing API key before any request.
- [ ] Implement the queue-first layout selected in `docs/design/concept-selection.md`, with advanced settings collapsed.
- [ ] Add constraint-driven CSS: stacked cards below 600 px, reflowed actions at 600-839 px, structured rows at 840 px and above, minimum 48 px controls, visible focus, and safe wrapping.
- [ ] Add Playwright checks at 320×640, 390×844, 600×900, and 1024×1366 with normal and 1.3-equivalent text scaling; assert no horizontal overflow and reachable primary actions for all queue states.
- [ ] Run UI unit and responsive tests and commit with `feat: add responsive audiobook queue ui`.

### Task 6: Reproducible launch, documentation, and full verification

**Files:**
- Modify: `docker-compose.webui.yml`
- Create: `.env.example`
- Modify: `.gitignore`
- Modify: `README.md`
- Create: `tests/integration/test_queue_flow.py`

**Interfaces:**
- Consumes: the completed queue application.
- Produces: `docker compose up --build` local workflow and French test instructions.

- [ ] Write a mocked end-to-end test that imports two EPUBs, completes one, fails and retries the other, and verifies one M4B path per completed job without a live TTS call.
- [ ] Add persistent `audiobook_data` volume mapping, health-oriented startup command, documented environment variables, and ignored local data/secrets.
- [ ] Rewrite the README quick start in French with OpenAI and free Edge paths, voice preview, estimated costs, M4B locations, pause/resume, and troubleshooting.
- [ ] Run `python -m pytest -q`, the Playwright matrix, and `docker compose config`.
- [ ] Build and start Docker, exercise upload/analysis with the example EPUB, visually smoke compact Android, iPhone reference, tablet portrait, and tablet landscape, then stop the stack.
- [ ] Commit with `docs: add audiobook queue quick start`.

### Task 7: GitHub delivery

**Files:**
- Modify: Git remotes only.

**Interfaces:**
- Produces: fork under `Jeanlemignon97`, pushed implementation branch, and a clean local worktree.

- [ ] Create or verify the GitHub fork of `p0n1/epub_to_audiobook` under `Jeanlemignon97`.
- [ ] Rename the upstream remote to `upstream` and set `origin` to the fork using `ssh://git@github.com/Jeanlemignon97/epub_to_audiobook.git`.
- [ ] Push the completed branch and verify the remote commit SHA.
- [ ] Report the repository URL, exact test commands, required API-key setup, and output locations.
