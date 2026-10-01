# Custom Front Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer Streamlit par un atelier documentaire maison, coloré et aéré, en conservant les parcours actuels.

**Architecture:** FastAPI sert les fichiers statiques et une API de même origine. Les sessions sont gérées côté serveur ; les fonctions actuelles d'ingestion et de RAG restent le moteur. HTML, CSS et JavaScript natifs ne nécessitent aucun service externe ou compilation frontend.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, python-multipart, bcrypt, PyYAML, pytest, HTTPX ; navigateur avec Fetch et ReadableStream.

**Spec:** `docs/superpowers/specs/2026-09-30-custom-front-design.md`, approuvée par Nicolas Riault.

## Global Constraints

- Interface française, responsive et utilisable au clavier.
- Bibliothèque à gauche, conversation au centre, sources à droite.
- Fond lavande clair, encre prune, accents corail et citron ; valeurs et composants décrits dans `DESIGN.md`.
- Sans bibliothèque chargée par CDN, sans compilation Node nécessaire et sans police distante.
- Réutiliser les identifiants et hashes bcrypt de `config.yaml`.
- Ne pas enregistrer de clé API dans le stockage persistant du navigateur.
- La base documentaire reste partagée comme dans l'application actuelle, sans ajout de tenancy.
- Pas de stockage permanent de conversations ajouté dans cette refonte.
- Les actions qui modifient l'état exigent une protection CSRF.
- Les fonctions synchrones et l'indexation ne doivent pas bloquer la boucle du serveur.
- Ce dossier n'a pas de dépôt Git utilisable : sauvegarder sur disque, sans initialiser un dépôt ni inventer des commits.

## Review Focus

1. Session expirée pendant une action : retour 401, retour à la connexion et message explicite, sans perdre silencieusement la question.
2. Échec après plusieurs fragments : conserver le texte partiel, indiquer l'interruption et libérer l'état occupé.
3. Import partiellement réussi : montrer le résultat de chaque fichier et actualiser la bibliothèque pour les succès.
4. Nom ou extrait contenant du HTML : afficher du texte, sans exécution ni insertion HTML non fiable.
5. Serveur de modèles indisponible : conserver l'accès aux réglages et à la navigation pour corriger la configuration.

## File Structure

- `auth.py` : lecture de configuration, vérification bcrypt, sessions, expiration, CSRF.
- `app.py` : application FastAPI, routes, validation des requêtes, adaptation des fonctions synchrones et flux de discussion.
- `static/index.html` : connexion et atelier, landmarks et contrôles accessibles.
- `static/styles.css` : tokens OKLCH, états, composition et responsive.
- `static/app.js` : session, navigation, documents, réglages, historique, sources et consommation du flux.
- `tests/test_auth.py` et `tests/test_web.py` : contrats des nouveaux modules, doubles du moteur local.
- `requirements.txt` et `README.md` : dépendances et lancement de la nouvelle interface.
- `ingest.py`, `rag.py`, `providers.py`, `bm25.py` : préserver leurs contrats ; toute adaptation nécessaire se limite à la gestion effective du flux.

### Task 1: Authentification et session

**Files:** créer `auth.py`, `tests/test_auth.py` ; modifier `requirements.txt`.

**Interfaces:** `load_auth_config(path: Path) -> dict`, `Session` avec `username`, `name`, `csrf_token`, `expires_at` ; `SessionStore.create(username: str, name: str, expiry_days: float) -> tuple[str, Session]`, `get(token: str) -> Session | None`, `revoke(token: str) -> None` ; `verify_credentials(config: dict, username: str, password: str) -> tuple[str, str] | None`. Configuration invalide : exception `AuthConfigError`, message sans données confidentielles.

- [ ] Écrire les tests `test_valid_bcrypt_login`, `test_wrong_or_unknown_credentials`, `test_invalid_config_is_presentable`, `test_session_expiry_and_revocation`. Assertions : credentials valides donnent l'identité ; inconnu et mauvais mot de passe ont la même erreur publique ; configuration placeholder ne donne pas de session ; après expiration ou révocation, `get` retourne `None`.
- [ ] Exécuter `rtk python -m pytest tests/test_auth.py -q` et constater les échecs sur le module absent.
- [ ] Implémenter ces interfaces avec `secrets.token_urlsafe`, expiration vérifiée côté serveur et validation du hash et de la durée configurée ; ajouter bcrypt comme dépendance directe.
- [ ] Réexécuter les tests et confirmer leur réussite. Conserver la configuration existante intacte.

### Task 2: Serveur et API documentaires

**Files:** remplacer `app.py`, créer `tests/test_web.py`, modifier `requirements.txt`.

**Interfaces:** exporter `app: FastAPI` et `create_app(config_path: Path | None = None) -> FastAPI` pour les tests. Réutiliser les interfaces de Task 1 et `ModelSettings`, `default_settings`, `list_models`, `ingest_document`, `list_documents`, `delete_document`.

**Contrat HTTP:**

| Route | Entrée | Résultat |
| --- | --- | --- |
| `GET /` | aucune | page statique |
| `POST /api/login` | JSON `username`, `password` | cookie opaque + identité + `csrf_token` |
| `GET /api/session` | cookie | identité + `csrf_token` + réglages par défaut sans clé |
| `POST /api/logout` | cookie + `X-CSRF-Token` | révocation et suppression du cookie |
| `POST /api/models` | réglages | listes `chat` et `embedding` |
| `POST /api/documents/list` | réglages | tableau `documents` sérialisé |
| `POST /api/documents/upload` | multipart `files`, JSON `settings` | tableau `results` avec nom, succès, fragments ou erreur |
| `POST /api/documents/delete` | JSON `source`, `settings` | succès |

Toutes les routes API sauf login nécessitent la session. Toutes les routes POST authentifiées exigent `X-CSRF-Token` ; login exige une origine cohérente lorsqu'un en-tête Origin est présent. Pas de CORS ouvert. Valider les réglages via `ModelSettings`, ne jamais sérialiser `api_key` dans les réponses. Accepter uniquement les formats spécifiés pour l'import. Exécuter les fonctions du moteur dans le pool de threads. Les erreurs attendues produisent un JSON `detail` lisible ; les exceptions imprévues donnent un message générique.

- [ ] Écrire les tests de connexion, cookies HttpOnly/SameSite, Secure en HTTPS, session, révocation et CSRF ; `test_session_expired_returns_401`, `test_partial_upload_results`, `test_model_server_failure_keeps_session`, `test_provider_key_not_echoed`. Assertions : 401 sans session, 403 sans CSRF, 422 pour réglages invalides, résultat individuel pour chaque fichier d'un lot mixte, aucune clé dans la réponse.
- [ ] Exécuter `rtk python -m pytest tests/test_web.py -q` et constater les échecs sur les routes absentes.
- [ ] Implémenter les routes, fournir les statiques et ajouter FastAPI, Uvicorn et python-multipart ; retirer Streamlit et streamlit-authenticator uniquement après remplacement effectif de leurs usages.
- [ ] Réexécuter `rtk python -m pytest tests/test_auth.py tests/test_web.py -q`. Confirmer isolation de session et conservation de la base partagée.

### Task 3: Réponses progressives et citations

**Files:** modifier `app.py`, `tests/test_web.py`.

**Interfaces:** `POST /api/chat` prend `question`, `history` (messages `role` user/assistant et `content`), `settings`, `top_k` (1 à 15, défaut 5), `similarity_cutoff` (0 à 1, défaut 0.2). Réutiliser `answer_question` et `RagResponse`.

Le flux est du NDJSON UTF-8 : `{"type":"sources","sources":[...]}`, `{"type":"chunk","text":"..."}`, `{"type":"error","message":"..."}`, `{"type":"done"}`. La recherche précède l'événement sources. Chaque source conserve `document`, `page`, `excerpt`, `score`. Une erreur de flux termine la réponse sans événement done. Valider la question et l'historique avant d'envoyer les en-têtes ; aucun fragment brut de fournisseur n'est exposé.

- [ ] Écrire `test_chat_stream_contains_sources_chunks_done`, `test_midstream_failure_emits_safe_error`, `test_chat_validation`, `test_disconnect_releases_iterator`. Utiliser un faux `RagResponse` ; vérifier ordre des événements, message d'erreur sans secret, fermeture du générateur et conservation des champs de source.
- [ ] Exécuter `rtk python -m pytest tests/test_web.py -q`, constater l'échec des nouveaux tests.
- [ ] Implémenter l'adaptation du générateur en thread et sa fermeture dans un finally ; une déconnexion doit libérer le flux lorsque l'appel fournisseur en cours rend la main.
- [ ] Réexécuter les tests et vérifier qu'une génération qui échoue n'empêche pas la suivante.

### Task 4: Atelier visuel et interactions

**Files:** créer `static/index.html`, `static/styles.css`, `static/app.js` ; compléter `tests/test_web.py` pour la disponibilité des ressources locales.

**Interfaces:** consommer les routes Tasks 2 et 3 ; état en mémoire uniquement : identité, CSRF, réglages, documents, messages achevés, réponse active, sources sélectionnées. Implémenter `api(path, options)`, `consumeChatStream(response, onEvent)`, `renderDocuments(documents)`, `renderMessages(messages)`, `renderSources(sources)`, `setView(view)` dans `static/app.js`.

- [ ] Construire HTML et CSS selon `DESIGN.md` : connexion, bibliothèque, accueil de conversation, réponses, sources, compositeur et réglages. Utiliser des SVG locaux et des labels réels, une liste de documents plutôt qu'une grille de cartes.
- [ ] Relier connexion/déconnexion, modèles, import, suppression confirmée inline, effacement de conversation et citations de chaque réponse. L'indisponibilité des modèles laisse les réglages accessibles ; changement d'index annonce la réimportation.
- [ ] Implémenter le flux avec décodage UTF-8 incrémental et tampon de lignes NDJSON, y compris lignes réparties sur plusieurs lectures ; refuser le second envoi tant qu'une réponse est active. Conserver la question en cas de 401, conserver le texte partiel en cas d'erreur et ne pas l'ajouter à l'historique achevé.
- [ ] Rendre tous les contenus non fiables avec `textContent` et création de nœuds DOM. Aucun HTML de document, nom de fichier ou message n'est injecté. Ne pas utiliser localStorage pour les réglages ou la clé.
- [ ] Réaliser les vues mobile et intermédiaire avec accès explicite aux trois zones, navigation au clavier, retour du focus après confirmation et annonces d'état sobres. Respecter prefers-reduced-motion.
- [ ] Vérifier en navigateur avec API doublée : session expirée, flux interrompu après texte, lot d'import mixte, nom `<img src=x onerror=alert(1)>`, serveur de modèles indisponible. Chaque cas respecte Review Focus et aucun script documentaire ne s'exécute.
- [ ] Vérifier à 390 px, 768 px et 1440 px : pas de débordement horizontal, contrôles accessibles, zones correctement distribuées. Inspecter contrastes et états focus/hover/disabled/loading. Corriger les problèmes observés.
- [ ] Exécuter `rtk node --check static/app.js` et les tests serveur sur la page et les fichiers statiques. Résultat attendu : syntaxe valide et fichiers disponibles sans authentification ; données API protégées.

### Task 5: Lancement, preuve finale et documentation

**Files:** modifier `README.md`, vérifier `requirements.txt`, rafraîchir `graft/`.

- [ ] Documenter installation et lancement `python -m uvicorn app:app --host 127.0.0.1 --port 8501`, configuration bcrypt conservée et tunnel sur ce même port. Documenter cookies Secure derrière HTTPS et confiance des en-têtes proxy uniquement depuis le proxy local autorisé.
- [ ] Documenter la connexion, l'import, les réglages oMLX/Ollama, citations, index par embeddings et erreurs courantes. Remplacer la section obsolète décrivant le front Streamlit.
- [ ] Exécuter `rtk python -m pytest -q` et confirmer la suite complète. Vérifier que le frontend ne charge aucune ressource externe dans l'onglet réseau du navigateur.
- [ ] Lancer l'application par la commande documentée, vérifier la connexion et les états de configuration dans le navigateur. Effectuer un essai RAG réel si serveur et modèles sont disponibles ; sinon rapporter précisément la limite, sans assimiler doubles et génération réelle.
- [ ] Exécuter `rtk graft build` puis relire le résultat. Examiner les fichiers modifiés sans toucher aux changements étrangers à la refonte.
- [ ] Livrer les liens vers l'interface et les fichiers principaux, le résultat des vérifications et les éventuelles limites réelles.

## Self-review et handoff

Couverture vérifiée : connexion/session (Tasks 1 et 2), bibliothèque et réglages (Tasks 2 et 4), chat et sources (Tasks 3 et 4), sécurité des contenus et responsive (Task 4), documentation et vérification du moteur (Task 5). Les cinq conditions de Review Focus ont une vérification affectée. Les signatures et champs HTTP sont cohérents entre tâches.

Plan prêt à être revu. Recommandation : exécution native dans cette conversation, car les tâches partagent une API courte et le périmètre forme une seule refonte. Une exécution avec sous-agents et revues intermédiaires reste possible si l'utilisateur la choisit explicitement.
