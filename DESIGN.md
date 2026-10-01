# Atelier documentaire DocMind

## Scene

Une personne travaille en journée sur ses documents, dans une pièce éclairée, et alterne lecture attentive et recherche. Le thème clair facilite les longues lectures ; les couleurs identifient les actions et les zones de travail.

## Color strategy

Palette à rôles nommés, avec surfaces pâles et accents saturés réservés aux actions ou sélections. Toutes les valeurs sont exprimées en OKLCH.

- Papier lavande : `oklch(97% 0.012 305)`.
- Surface de lecture : `oklch(99% 0.004 305)`.
- Encre prune : `oklch(27% 0.055 320)`.
- Encre secondaire : `oklch(47% 0.035 320)`.
- Prune action : `oklch(44% 0.16 320)`.
- Corail : `oklch(74% 0.14 35)`, associé à une encre sombre.
- Citron : `oklch(91% 0.14 105)`, associé à une encre sombre.
- Séparateurs : `oklch(87% 0.018 305)`.

## Typography

Police système locale. Corps 16 px, labels 14 px, titres de section 20 px, titre d'accueil 36 px. Texte de réponse limité à 72 caractères environ par ligne. Graisses et tailles portent la hiérarchie.

## Layout

Un bandeau commun porte le projet actif et ses actions de création et de gestion. Sur grand écran, bibliothèque de 270 px, conversation flexible et panneau de sources de 290 px, avec espacements de 16 à 40 px selon le rôle. Deux vues, Discussion et Résumés audio, séparent les activités. La vue audio occupe l’espace central et celui des sources ; ses textes restent limités à une largeur de lecture. Le changement de vue conserve le brouillon et les échanges.

Pas de grille de cartes. Les documents sont une liste et les citations une liste de passages. À largeur intermédiaire, les sources se dévoilent dans une zone dédiée. Sur mobile, Documents, Discussion, Audio et Sources sont accessibles par une navigation explicite en bas d’écran, sans défilement horizontal. Le projet actif reste visible au-dessus de chaque vue.

## Components

Boutons aux coins arrondis, contrôles standards, sélection et focus visibles. Zone d'import avec bouton de sélection en plus du glisser-déposer. Réglages dans un panneau dépliable. Actions de projet dans des menus dépliables, fermés par Échap ou un clic extérieur. Suppression confirmée à côté du document ou dans la gestion du projet. État vide proposant l'import comme première action. Réponses et sources liées visuellement sans encarts imbriqués. Réponses et transcriptions en 16 px, noms de documents et extraits de sources en 14 px. Résumés audio listés avec lecteur, téléchargement et transcription dépliable.

## Motion

Transitions de couleur et d'opacité de 180 ms avec ease-out. Pas d'animation décorative ou de propriétés de disposition. Respect de prefers-reduced-motion.
