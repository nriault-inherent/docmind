# DocMind

Une base documentaire privée avec recherche RAG, discussions sourcées et résumés
audio dans une interface web. FastAPI, LlamaIndex et ChromaDB assurent le moteur ;
le front fonctionne sans CDN et ne nécessite pas Node en production.

DocMind se connecte à **oMLX**, **Ollama**, ou une **API compatible OpenAI Chat
Completions ou Anthropic Messages**, locale ou distante. Une API distante reçoit
les textes envoyés pour les embeddings et les passages utilisés pour répondre.
Avec des serveurs locaux, ces traitements restent sur votre machine.

## Démarrage

Python 3.11 à 3.13 et un serveur de modèles sont nécessaires. oMLX nécessite
macOS Apple Silicon ; DocMind et les autres API ne sont pas limités à macOS.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/init_auth.py
python -m uvicorn app:app --env-file .env --host 127.0.0.1 --port 8501
```

L’assistant demande un identifiant et un mot de passe masqué et crée `config.yaml`
avec un hash bcrypt et des permissions privées. Aucun compte utilisable n’est
fourni. Vous pouvez aussi copier `config.example.yaml` et ajouter vos propres
utilisateurs et hashes bcrypt. `DOCMIND_CONFIG` choisit un autre fichier ; passez
le même chemin à `scripts/init_auth.py --config CHEMIN`.

Ouvrez [l’application locale](http://127.0.0.1:8501), connectez-vous, puis ouvrez
**Réglages**. Choisissez le protocole, l’URL et les modèles de réponse et
d’indexation. Les identifiants peuvent aussi être saisis dans « Saisir des identifiants de modèles manuellement » : le catalogue
est une aide, il n’est pas obligatoire. Appliquez les réglages avant d’importer.

`.env` est chargé par `uvicorn --env-file .env`. Sans cette option, exportez les
variables dans votre environnement avant le lancement.

## Brancher un serveur LLM

| Serveur | Protocole | Adresse d’exemple |
| --- | --- | --- |
| oMLX | `openai` ou `anthropic` | `http://127.0.0.1:11435/v1` |
| Ollama natif | `ollama` | `http://localhost:11434` |
| Ollama compatible OpenAI | `openai` | `http://localhost:11434/v1` |
| API ou passerelle OpenAI | `openai` | `https://api.example.com/v1` |
| API Anthropic | `anthropic` | `https://api.anthropic.com/v1` |

Renseignez dans `.env` `DOCMIND_PROTOCOL`, `DOCMIND_BASE_URL`, `DOCMIND_LLM_MODEL`,
`DOCMIND_EMBED_MODEL` et, si nécessaire, `DOCMIND_API_KEY`. Laissez les modèles
vides pour les choisir dans l’interface.

Le RAG a besoin d’un **modèle d’embeddings**, distinct du modèle de réponse.
Si le fournisseur de chat n’en propose pas, configurez une autre API avec
`DOCMIND_EMBED_BASE_URL`, `DOCMIND_EMBED_PROTOCOL` (`openai` ou `ollama`) et
`DOCMIND_EMBED_API_KEY`. Ces champs sont aussi disponibles dans les réglages
avancés. Une URL d’indexation séparée n’hérite jamais de la clé du chat.

Les exemples complets, options et limites de compatibilité figurent dans
[docs/LLM_APIS.md](docs/LLM_APIS.md).

## Documents, projets et discussions

Créez un projet, importez des PDF, DOCX, TXT, Markdown ou HTML (200 Mo maximum
par fichier), puis posez des questions. Les réponses s’affichent progressivement
avec leurs sources. Entrée envoie, Maj + Entrée ajoute une ligne. Vérifiez les
sources et les réponses ; le contenu des documents peut être malveillant.

Les nouveaux projets sont privés au compte qui les crée. Documents, conversations
et résumés audio restent liés au projet côté serveur. L’espace historique
**Général** reste partagé entre les comptes configurés. Les discussions sont
conservées entre les redémarrages. Une réponse interrompue reste visible mais
n’est pas réutilisée comme un échange terminé.

Chaque adresse d’API d’embeddings et modèle d’indexation possède son index.
Changer de modèle de réponse ne nécessite pas de réindexation. Changer d’API
ou de modèle d’embeddings nécessite de réimporter les documents dans le nouvel
index ; les anciens index restent conservés. Le mode natif Ollama retrouve
l’ancien index Général uniquement si son URL et son modèle correspondent à
`BASE_URL` et `EMBED_MODEL`, les paramètres historiques d’indexation.

La recherche propose de 1 à 15 passages et un seuil de pertinence de 0 à 1.
Le score classe les passages ; il ne mesure pas la certitude d’une réponse.
Supprimer un projet supprime ses index, conversations et résumés audio.

## Résumés audio

Les niveaux Bref, Standard et Détaillé produisent une synthèse du corpus du
projet, puis une narration WAV avec transcription et sources. Cette fonction
nécessite un serveur offrant `/v1/audio/speech` compatible avec le payload oMLX
et une sortie WAV. Elle n’est pas disponible en mode natif Ollama. Le modèle
vocal est modifiable dans les réglages. Sa présence dans le catalogue ne
prouve pas qu’il peut être chargé : validez d’abord la synthèse côté serveur.

## Données et sécurité

`data/chroma_db/` contient les index. `data/projects/` contient SQLite, les
discussions et les WAV. Sauvegardez les deux dossiers ensemble, application
arrêtée. `DOCMIND_CHROMA_PATH` et `DOCMIND_PROJECTS_PATH` déplacent ces stockages.

Données, `.env`, `config.yaml`, caches, réglages personnels d’outils et fichiers
de clés sont exclus de Git. Les exemples ne contiennent aucun mot de passe,
hash utilisable ou clé API. Voir [SECURITY.md](SECURITY.md).

Les clés saisies restent en mémoire dans la page, sans stockage navigateur.
Les clés d’environnement ne sont pas retournées à l’interface. Changer l’adresse
ou le protocole du chat ne transmet pas sa clé serveur au nouveau fournisseur :
saisissez une clé propre au fournisseur choisi.

Les sessions utilisent un cookie opaque HttpOnly et SameSite=Lax, avec contrôle
CSRF et d’origine. Un redémarrage révoque les sessions. Utilisez **un seul worker
Uvicorn** : les sessions sont en mémoire. Redémarrez après modification des
utilisateurs dans `config.yaml`. Pour exposer l’application, utilisez un proxy
HTTPS et ne faites confiance qu’aux adresses de vos proxies pour les en-têtes
transférés. Le cookie Secure dépend de la reconnaissance correcte de HTTPS.

## Développement

```bash
pip install -r requirements-dev.txt
python -m pytest
node --test tests/test_front_state.mjs tests/test_stream.mjs
ruff check .
```

Les tests utilisent des configurations temporaires, des transports HTTP simulés
et des bases Chroma temporaires. Ils ne prouvent pas la disponibilité réelle
d’un fournisseur, la capacité mémoire d’un modèle ou la compatibilité d’un TTS.

Le moteur est dans `providers.py`, `ingest.py` et `rag.py` ; `app.py` expose l’API,
`auth.py` gère les sessions, `workspace.py` et `projects.py` gèrent les projets
et historiques, `audio_summary.py` gère l’audio. `static/` contient l’interface
et `tests/` les vérifications.
