# Refonte du front DocMind

## Intention et périmètre validés

Remplacer le front Streamlit par une interface web maison, originale, audacieuse, colorée, professionnelle et aérée. Direction visuelle validée par l'utilisateur : fond lavande clair, encre prune, accents corail et citron ; bibliothèque à gauche, conversation au centre, sources à droite. Interface française, responsive et utilisable au clavier. Réutiliser le moteur documentaire existant.

Il s'agit d'une évolution architecturale de la couche de présentation : la session et les interactions aujourd'hui gérées par Streamlit deviennent des échanges entre le navigateur et un serveur Python.

## Architecture proposée

Un serveur FastAPI fournit l'interface et une API de même origine. Le front est constitué de HTML, CSS et JavaScript natifs, sans bibliothèque chargée par CDN, sans compilation Node nécessaire et sans police distante. Ce choix donne la liberté graphique attendue tout en conservant un lancement Python simple. Un front React ajouterait un outillage superflu pour ces parcours ; personnaliser Streamlit ne réaliserait pas le remplacement validé.

- `app.py` devient le point d'entrée du serveur web.
- Un module dédié à l'authentification gère les utilisateurs configurés et les sessions.
- `static/` contient la page, les styles, les interactions et les icônes locales.
- `ingest.py`, `rag.py`, `providers.py` et `bm25.py` conservent leurs responsabilités et leurs contrats.
- `requirements.txt` et `README.md` décrivent le nouveau lancement et les dépendances effectivement utilisées.
- Les fichiers `PRODUCT.md` et `DESIGN.md` fixent le contexte et la direction visuelle.

## Parcours

### Connexion

Écran de connexion cohérent avec l'atelier documentaire. Réutiliser les identifiants et hashes bcrypt de `config.yaml`. Une configuration incomplète donne une erreur compréhensible, sans exposer de secret. La déconnexion révoque la session. L'ensemble des routes documentaires, modèles et discussion exige une session authentifiée.

### Bibliothèque

Import par sélection ou glisser-déposer, plusieurs fichiers possibles. Formats actuels : PDF, DOCX, TXT, MD, Markdown, HTML et HTM. L'indexation affiche le fichier en cours, les succès et les échecs individuellement. La liste affiche nom, nombre de fragments, pages et date lorsque ces informations existent. Une confirmation inline précède la suppression. L'échec de suppression laisse le document visible et donne une action de reprise.

### Discussion

Le champ de question reste accessible près du bas de la conversation. Une réponse apparaît progressivement, avec indication textuelle de la recherche puis de la rédaction. Une seule génération active par conversation. Les erreurs restent visibles et permettent une nouvelle tentative. Effacer la conversation remet à zéro l'historique et les sources. Pas de stockage permanent de conversations ajouté dans cette refonte.

### Sources

Chaque réponse conserve ses sources. Sélectionner une réponse ou son bouton de sources affiche ses passages dans le panneau correspondant : document, page éventuelle, extrait et score disponible. Le score est présenté comme un score de pertinence, jamais comme une probabilité de vérité. Sur mobile, les passages se consultent dans une vue accessible puis on revient à la discussion.

### Réglages

Panneau dépliable : protocole OpenAI/oMLX, Anthropic/oMLX ou Ollama, adresse du serveur, clé optionnelle, modèles disponibles, actualisation, longueur de réponse, nombre de fragments et seuil de pertinence. Conserver les valeurs par défaut actuelles. Ne pas enregistrer de clé API dans le stockage persistant du navigateur. Les changements de modèle qui invalident le contexte effacent la conversation comme aujourd'hui. Un changement de modèle d'embeddings explique la nécessité de réimporter les documents ; OpenAI et Anthropic gardent le partage actuel des index oMLX.

## Échanges serveur

Routes de connexion, session et déconnexion ; découverte des modèles ; liste, import et suppression des documents ; génération de réponse. Les paramètres passent par la validation de `ModelSettings`. L'historique envoyé au moteur se limite aux messages achevés de la conversation en cours.

La réponse utilise un flux HTTP consommé par le navigateur, avec événements distincts pour les sources, les fragments de texte, les erreurs et la fin. Les fonctions synchrones et l'indexation ne doivent pas bloquer la boucle du serveur. Une déconnexion du client ferme le flux et libère les ressources lorsque le fournisseur le permet.

## Sessions et données

Sessions serveur avec identifiant aléatoire opaque dans un cookie HttpOnly, SameSite et limité à la durée configurée. Le cookie est Secure lorsque l'application est servie en HTTPS. Prévoir un fonctionnement derrière le tunnel documenté. Les actions qui modifient l'état exigent une protection CSRF. Les clés de session et de fournisseur ne sont jamais renvoyées dans une réponse ni inscrites dans les logs.

Les messages et extraits sont rendus avec échappement ; aucun HTML documentaire n'est exécuté. Les noms de fichiers restent traités par la validation d'ingestion existante. Les configurations des utilisateurs sont isolées par session ; la base documentaire reste partagée comme dans l'application actuelle, sans ajout de tenancy.

## États et adaptation

Connexion en cours, identifiants erronés, configuration invalide, serveur de modèles indisponible, modèles absents, bibliothèque vide, import en cours, import partiellement réussi, recherche en cours, génération en cours, génération interrompue et absence de source pertinente sont tous représentés. L'indisponibilité du serveur de modèles ne masque pas la navigation ni les réglages permettant de la corriger.

Grand écran : trois zones. Écran intermédiaire : conversation prioritaire et sources accessibles sur demande. Mobile : navigation entre bibliothèque, discussion et sources. Pas de modal requis pour les actions courantes, pas de défilement horizontal. Les contrôles ont des labels, le focus est visible et les changements d'état utiles sont annoncés aux lecteurs d'écran sans lire chaque fragment de génération.

## Validation et critères d'acceptation

- Exécuter la suite existante pour préserver ingestion, retrieval et fournisseurs.
- Vérifier connexion, accès refusé sans session, CSRF et déconnexion avec des tests de serveur.
- Tester import, liste, suppression et flux de réponse à l'aide de doubles du moteur local, incluant un échec de flux.
- Vérifier visuellement l'interface sur ordinateur et mobile, avec bibliothèque vide puis peuplée, réponse et citations.
- Vérifier navigation au clavier, focus, absence de débordement, contraste et réduction des animations.
- Vérifier le lancement documenté et l'absence de ressources externes chargées par le front.
- Distinguer explicitement les tests avec doubles d'un essai de génération réel ; un essai réel dépend de la disponibilité du serveur local et des modèles.
- Rafraîchir le graphe graft après les changements de code.

## Limites

Pas de nouveau moteur RAG, de synchronisation cloud, de comptes créables depuis le front, de conversations persistantes, de nouveau format documentaire ou de déploiement externe. Aucun changement des identifiants existants ni migration de la base documentaire n'est requis par la refonte.

## État

Direction et spécification approuvées dans la conversation par Nicolas Riault. Code applicatif pas encore modifié. Ce dossier ne contient actuellement pas de dépôt Git utilisable, donc la spécification est sauvegardée sur disque sans commit.
