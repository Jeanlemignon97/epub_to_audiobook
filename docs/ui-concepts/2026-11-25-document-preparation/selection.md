# Préparer un livre : choix de l'interface

L'interface existante emploie un fond bleu nuit, des cartes aux angles arrondis, un accent ambre, des formulaires en colonne sur téléphone et une largeur de lecture plafonnée sur grand écran.

| Concept | Points forts | Limites |
| --- | --- | --- |
| [1 — deux parcours](concept-1.png) | Choix immédiat entre audio et préparation ; formulaire court ; cohérent avec l'écran actuel ; facile à replier sur 320 px. | L'estimation de la traduction doit être présentée dans le formulaire. |
| [2 — assistant](concept-2.png) | Vérification du texte et du coût très lisible avant lancement. | Plus d'étapes et plus d'interactions pour une simple conversion PDF. |
| [3 — atelier](concept-3.png) | Avancement et historique très visibles sur grand écran. | Trop dense pour les téléphones et l'usage occasionnel. |

**Choix : concept 1.** L'utilisateur avait autorisé l'approbation automatique des recommandations. Le formulaire affiche une analyse et une estimation avant la traduction, puis laisse lancer une préparation persistante. Les fichiers EPUB obtenus sont directement téléchargeables et peuvent ensuite être envoyés à la conversion audio existante. La mise en œuvre conserve les composants Gradio et les couleurs du produit.
