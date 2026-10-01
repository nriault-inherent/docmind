# Connecter une API LLM

## Contrats supportés

DocMind envoie des requêtes HTTP aux routes suivantes, relatives à l’adresse
configurée. Utilisez la racine versionnée exacte de votre API. Pour OpenAI et
Anthropic, une URL sans chemin reçoit automatiquement `/v1` ; un chemin fourni
explicitement est conservé (par exemple `https://gateway.example/api/llm`).

| Protocole | Catalogue | Génération | Flux | Embeddings |
| --- | --- | --- | --- | --- |
| `openai` | `GET /models` | `POST /chat/completions` | SSE, `choices[].delta.content` | `POST /embeddings` |
| `anthropic` | `GET /models` | `POST /messages` | SSE, `text_delta` et `message_stop` | API séparée compatible OpenAI ou Ollama |
| `ollama` | `GET /api/tags` | `POST /api/chat` | NDJSON, `message.content` et `done` | `POST /api/embed` |

Sans API d’indexation séparée, les modes OpenAI et Anthropic appellent
`/embeddings` sur le même serveur, ce qui fonctionne notamment avec oMLX.
Pour un fournisseur Anthropic sans cette route, renseignez une API d’embeddings
distincte. L’authentification OpenAI/Ollama utilise `Authorization: Bearer`,
Anthropic utilise `x-api-key` et `anthropic-version`.

Une API propriétaire doit être adaptée à l’un de ces contrats, par exemple via
une passerelle. Les API Responses, Azure à URL de déploiement et en-tête
`api-key`, OAuth, signatures AWS, outils et entrées multimodales ne sont pas
prises en charge directement. « Compatible OpenAI » ne garantit pas que tous
les modèles acceptent les mêmes paramètres.

## oMLX

Installez [oMLX](https://github.com/jundot/omlx), lancez-le et chargez un modèle
de génération et un modèle d’embeddings. Choisissez leurs noms/aliases exacts
dans les réglages ou `.env` :

```dotenv
DOCMIND_PROTOCOL=openai
DOCMIND_BASE_URL=http://127.0.0.1:11435/v1
DOCMIND_LLM_MODEL=mon-modele-chat
DOCMIND_EMBED_MODEL=mon-modele-embedding
DOCMIND_API_KEY=
```

Le mode `anthropic` utilise la même adresse pour oMLX. Pour l’instance locale
sur localhost/127.0.0.1:11435, DocMind désactive `enable_thinking` en OpenAI.
Si oMLX utilise un autre port/hôte, configurez explicitement
`DOCMIND_LLM_OPTIONS={"enable_thinking":false}`. Cette extension n’est pas
envoyée par défaut aux autres serveurs OpenAI.

## Ollama

Lancez Ollama et installez vos modèles (`ollama pull IDENTIFIANT`), puis :

```dotenv
DOCMIND_PROTOCOL=ollama
DOCMIND_BASE_URL=http://localhost:11434
DOCMIND_LLM_MODEL=mon-modele-chat
DOCMIND_EMBED_MODEL=mon-modele-embedding
```

Le mode natif utilise `options.num_predict` pour la limite de réponse et
`think=false`. Les modèles sont sélectionnables dans les listes déroulantes de l’interface.
Le passage entre les protocoles API et Ollama restaure leurs adresses et
réglages respectifs en mémoire dans la page. Vous pouvez
aussi utiliser `openai` avec `http://localhost:11434/v1` selon les fonctions
compatibles de votre version d’Ollama.

Références : [chat natif](https://docs.ollama.com/api/chat),
[embeddings](https://docs.ollama.com/api/embed),
[catalogue](https://docs.ollama.com/api/tags),
[compatibilité OpenAI](https://docs.ollama.com/api/openai-compatibility).

## API distante avec embeddings locaux

Exemple Anthropic avec Ollama pour l’indexation :

```dotenv
DOCMIND_PROTOCOL=anthropic
DOCMIND_BASE_URL=https://api.anthropic.com/v1
DOCMIND_API_KEY=
DOCMIND_LLM_MODEL=identifiant-du-modele-chat
DOCMIND_EMBED_BASE_URL=http://localhost:11434
DOCMIND_EMBED_PROTOCOL=ollama
DOCMIND_EMBED_MODEL=identifiant-du-modele-embedding
DOCMIND_EMBED_API_KEY=
```

Renseignez la clé réelle uniquement dans votre `.env` privé. Le chat reçoit les
passages sélectionnés et l’historique utile ; conserver les embeddings locaux
ne rend donc pas la génération distante confidentielle vis-à-vis du fournisseur.
Une adresse secondaire ne reçoit jamais la clé du chat, même si son champ clé
reste vide.

## Catalogues et paramètres spécifiques

La détection par nom (`embed`, `rerank`, etc.) et `model_type` reste indicative.
Un catalogue peut omettre les capacités ou être indisponible. Ouvrez alors « Saisir des identifiants de modèles manuellement » et saisissez
les identifiants exacts. Un nom personnalisé
est conservé lors de l’actualisation. La génération ne dépend pas du catalogue.

Les options avancées acceptent un objet JSON (`DOCMIND_LLM_OPTIONS` côté serveur).
Une valeur `null` retire un paramètre de la requête. `model`, `messages` et
`stream` sont réservés. Ne mettez jamais de secret dans cet objet.

Pour un modèle OpenAI utilisant `max_completion_tokens` et refusant temperature :

```dotenv
DOCMIND_LLM_OPTIONS={"max_tokens":null,"max_completion_tokens":2048,"temperature":null}
```

Ces valeurs explicites remplacent les paramètres par défaut : la limite de
l’interface ne remplace pas une limite fixée dans ce JSON. Pour Ollama, l’option
`options` remplace le bloc complet ; incluez `num_predict` si nécessaire.
Le mode Anthropic envoie `thinking={"type":"disabled"}` ; retirez ce champ avec
`{"thinking":null}` si votre API ne le supporte pas.

Voir les contrats officiels [OpenAI Chat Completions](https://platform.openai.com/docs/api-reference/chat)
et [Anthropic Messages](https://docs.anthropic.com/en/api/messages).

## Variables et compatibilité historique

Les variables `DOCMIND_*` ont priorité sur `OMLX_BASE_URL`, `OMLX_LLM_MODEL`,
`OMLX_EMBED_MODEL` et `OMLX_API_KEY`. En mode natif Ollama, `BASE_URL`,
`OLLAMA_LLM_MODEL` et `EMBED_MODEL` restent des valeurs de repli.
Les nouveaux paramètres ne convertissent pas les anciens vecteurs. Une nouvelle
adresse ou un nouveau modèle d’embeddings ouvre un index distinct ; réimportez
vos fichiers. Les clés et les paramètres de génération ne participent pas à
l’identité d’index. Les projets restent isolés, quel que soit le fournisseur.

Une erreur HTTP 401/403 indique notamment une clé ou des droits incorrects ;
404 peut indiquer une mauvaise racine d’API ou un modèle absent. Un HTTP 507
avec oMLX demande de vérifier les logs du serveur et son plafond mémoire.
DocMind ne renvoie pas le corps détaillé des erreurs fournisseur à l’utilisateur.
