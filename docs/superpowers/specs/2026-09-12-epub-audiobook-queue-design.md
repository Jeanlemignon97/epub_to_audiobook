# Conception de la file de conversion EPUB vers audiobook

## Objectif

Transformer localement plusieurs EPUB sans DRM en livres audio agréables à
écouter, avec une voix naturelle, un coût annoncé avant conversion et une
reprise fiable après interruption. Le texte reste lu dans l’application EPUB
habituelle de l’utilisateur ; la V1 ne synchronise pas le texte et l’audio.

## Périmètre de la V1

- Importer jusqu’à trois fichiers `.epub` par ajout depuis une interface web
  locale. Les conversions restent séquentielles dans la V1.
- Analyser le titre, l’auteur, le nombre de chapitres, le volume de texte, la
  durée vocale approximative et le coût estimé.
- Générer un extrait vocal court avant les conversions payantes.
- Traiter les livres en file d’attente, un livre à la fois par défaut.
- Afficher les états attente, analyse, conversion, pause, erreur et terminé.
- Reprendre une conversion en réutilisant chaque chapitre audio déjà produit.
- Produire un fichier `.m4b` par livre avec les chapitres EPUB comme marqueurs.
- Conserver les MP3 intermédiaires pour permettre réparation et reprise.
- Proposer OpenAI `gpt-4o-mini-tts` par défaut et conserver Edge, Azure et Piper
  comme alternatives supportées par le moteur existant.
- Fournir un lancement Docker reproductible et des instructions de test en
  français.

La traduction linguistique, le retrait de DRM, la synchronisation mot à mot,
les comptes utilisateurs, le stockage cloud et l’application mobile native
sont hors périmètre.

## Découpage audio

Le découpage technique reste fin : chaque chapitre est divisé en blocs adaptés
à la limite du fournisseur TTS. Ce découpage n’est pas exposé à l’utilisateur.
Les chapitres sont générés et mis en cache séparément, puis assemblés en un seul
M4B. Les chapitres courts restent des marqueurs internes ; ils ne deviennent
pas des fichiers visibles séparés dans le résultat final. Le coût dépend du
texte ou de l’audio généré et non du nombre de fichiers finaux.

## Architecture

L’outil Python existant reste le moteur de parsing et de synthèse. Une nouvelle
couche `queue` fournit trois unités isolées : un registre SQLite persistant,
une analyse EPUB et un exécuteur séquentiel. L’interface Gradio soumet les
fichiers au registre et interroge périodiquement les états. L’exécuteur lance le
moteur dans un sous-processus isolé, ce qui permet de l’arrêter proprement sans
bloquer l’interface.

Chaque livre possède un identifiant UUID et un répertoire privé sous
`audiobook_data/jobs/<id>/` contenant l’EPUB importé, les MP3 par chapitre, les
métadonnées d’analyse, les journaux et le M4B final. SQLite ne stocke aucun
secret. Les clés des fournisseurs restent uniquement dans les variables
d’environnement du processus.

## Flux de données

1. L’utilisateur dépose des EPUB ; chaque fichier est validé et copié avec un
   nom interne sûr.
2. L’analyse lit les métadonnées et le spine EPUB, ignore les documents vides,
   calcule les estimations, puis place le travail en attente.
3. L’utilisateur écoute éventuellement un extrait et lance ou reprend la file.
4. Le worker sélectionne le plus ancien travail en attente et démarre le moteur
   avec la voix choisie.
5. Avant chaque chapitre, le moteur vérifie si un fichier audio valide existe.
   Si oui, il le réutilise ; sinon, il le génère dans un fichier temporaire puis
   le renomme atomiquement.
6. Après le dernier chapitre, FFmpeg encode et assemble les pistes en AAC/M4B
   et injecte les marqueurs avec titres et durées.
7. L’interface expose le téléchargement du M4B et laisse disponibles les
   fichiers intermédiaires dans le dossier de données.

## Voix naturelle et maîtrise du coût

Le profil par défaut utilise `gpt-4o-mini-tts`, une vitesse de `1.0` et des
instructions de narration en français : ton chaleureux, posé et humain,
articulation claire, pauses naturelles entre paragraphes, absence de ton
publicitaire. Les instructions sont modifiables. Un aperçu emploie un passage
court du premier chapitre et ne démarre jamais la conversion complète.

L’analyse présente une estimation avec la mention qu’elle reste approximative.
Un plafond facultatif empêche le démarrage d’un livre dont l’estimation le
dépasse. Les erreurs API conservent les fichiers déjà terminés.

## Erreurs et reprise

- Un EPUB invalide ou vide est refusé avec un message lisible.
- Une clé absente est détectée avant le lancement d’un travail OpenAI ou Azure.
- Une erreur réseau place le travail en erreur et conserve son dernier chapitre
  terminé ; l’action Réessayer remet le travail en attente.
- Pause termine le groupe de processus du livre après avoir conservé les
  sorties atomiques déjà validées.
- Un fichier audio temporaire ou vide n’est jamais considéré comme terminé.
- FFmpeg absent ou défaillant produit une erreur dédiée sans supprimer les MP3.

## Interface responsive et accessible

Le concept sélectionné est documenté dans
`docs/design/concept-selection.md`. L’écran utilise une largeur de contenu
maximale, des cartes de file d’attente et des actions d’au moins 48 px. Sous
600 px, les métriques et actions passent sous les informations du livre. Entre
600 et 839 px, deux zones peuvent partager une ligne lorsque l’espace le
permet. À partir de 840 px, chaque travail devient une ligne structurée sans
étirer les textes. Aucun titre, coût, message d’erreur ni action principale
n’est tronqué.

La page reste utilisable à 320 × 640 avec une échelle de texte simulée à 1,3,
ainsi qu’à 390 × 844, 600 × 900 et 1024 × 1366. L’import et les actions restent
atteignables au clavier. Les états ne reposent pas uniquement sur la couleur.

## Tests et critères d’acceptation

- Tests unitaires du registre SQLite, des transitions d’état, des estimations,
  de la reprise par chapitre et de la génération des métadonnées FFmpeg.
- Tests d’intégration avec fournisseur TTS et sous-processus simulés : succès,
  pause, reprise, erreur puis nouvel essai.
- Test réel de parsing avec l’EPUB d’exemple du dépôt.
- Tests navigateur aux quatre largeurs requises, en portrait et paysage utile,
  avec texte normal et agrandi, plus états vide, en cours, erreur et terminé.
- Vérification qu’aucun appel TTS réel n’est effectué pendant la suite de tests.
- Smoke test Docker et vérification manuelle sur petit viewport Android et
  viewport iPhone de référence.

La V1 est acceptée lorsque plusieurs EPUB peuvent être ajoutés, qu’un travail
interrompu reprend sans régénérer les chapitres valides, qu’un M4B avec marqueurs
est produit par livre, que les coûts sont affichés avant démarrage et que les
tests automatisés ne signalent aucun débordement responsive.
