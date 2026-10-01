# Projets isolés dans DocMind

Date : 30 septembre 2026

## Objectif et périmètre validé

Un utilisateur sélectionne un projet, importe ses fichiers et mène plusieurs
conversations sur ces fichiers. Chaque projet possède ses documents et ses
conversations. Aucun contenu d'un autre projet ne doit intervenir dans une
réponse, une citation, un historique ou une suppression.

Le périmètre comprend la création, le renommage, la sélection et la suppression
des projets, ainsi que la création, la sélection et la conservation de plusieurs
conversations par projet. Les documents existants restent disponibles dans un
projet « Général ».

Les nouveaux projets reprennent les contrôles de propriété par compte ajoutés
par la fonction de résumé audio développée dans le même espace de travail.
« Général » préserve l'accès partagé des comptes autorisés à l'ancienne
bibliothèque. Cette évolution ne crée pas de permissions ou de partage configurable.

## État actuel vérifié

- `ingest.py:42-50` choisit la collection Chroma selon les réglages du modèle,
  sans identifiant de projet.
- `ingest.py:133-208` utilise cette collection pour importer, lister et supprimer.
- `rag.py:216-262` utilise cette même collection pour les recherches vectorielles
  et lexicales, puis construit le contexte et les citations.
- `app.py:65-116` reçoit l'historique de la conversation depuis le navigateur.
- `app.py:214-249` expose les opérations documentaires et la discussion sans
  rattachement à un projet.
- `static/app.js:329-384` construit cet historique depuis les messages en mémoire.

## Architecture retenue

### Projets et conversations persistants

Ajouter un module de stockage SQLite utilisant la bibliothèque standard Python,
intégré à `ProjectStore` pour préserver les résumés audio et utiliser une seule
base : `data/projects/projects.sqlite3`, configurable par `DOCMIND_PROJECTS_PATH`.

Tables :

- `projects` : identifiant UUID stable, nom non vide, dates de création et de
  modification, indicateur du projet de compatibilité « Général ».
- `conversations` : identifiant UUID, clé étrangère vers le projet, titre et dates.
- `messages` : conversation, ordre, rôle, contenu, citations et état de génération.

Activer les clés étrangères et utiliser des transactions. Les identifiants ne
sont jamais dérivés des noms affichés. Deux projets peuvent donc contenir un
document de même nom sans collision. Renommer un projet ne déplace pas ses données.

### Collections documentaires distinctes

Chaque nouveau projet utilise une collection Chroma propre pour chaque espace
d'embeddings déjà distingué par l'application. Le nom de collection est calculé
côté serveur à partir de l'identifiant stable du projet et de l'identité de
collection existante, avec un format compatible avec Chroma.

Le projet « Général » conserve les conventions de collections historiques. Il
est créé une seule fois de manière idempotente. Aucune réindexation ni suppression
des documents existants n'est nécessaire pour activer les projets.

Les imports, listes, suppressions, recherches vectorielles, recherches BM25 et
citations utilisent tous la collection du projet demandé. Ne pas dépendre d'un
filtre dans le navigateur pour garantir l'isolation.

### Contrôles côté serveur

Chaque opération documentaire exige un identifiant de projet existant. Chaque
discussion exige aussi une conversation appartenant à ce projet. Un identifiant
inconnu ou une conversation appartenant à un autre projet est rejeté avant tout
accès au modèle ou aux documents.

L'historique envoyé au modèle est chargé depuis les messages de cette conversation
dans SQLite. Le navigateur ne fournit plus un historique faisant autorité.
Conserver les contrôles d'authentification et de protection CSRF existants.

La réponse conserve le protocole de streaming actuel. Sauvegarder la question,
les citations et la réponse dans leur conversation d'origine. Une génération
interrompue ou échouée reste explicitement marquée ; une réponse partielle n'est
pas réutilisée comme réponse terminée dans les générations suivantes.

## Interface et parcours

Ajouter un sélecteur de projets dans l'espace de travail, avec création,
renommage et suppression. Afficher les conversations du projet actif et proposer
« Nouvelle conversation ». Utiliser la première question comme titre initial.

Lors d'un changement de projet, remplacer la liste des documents, la liste des
conversations, les messages et les citations. Les chargements asynchrones sont
rattachés à leur projet et conversation d'origine ; une réponse tardive ne doit
jamais remplacer l'affichage d'un autre projet.

Pendant un import ou une génération, désactiver les changements de contexte dans
ce navigateur. Les requêtes serveur restent explicitement rattachées à leur
contexte, y compris lorsque plusieurs onglets sont ouverts.

La sélection d'une conversation recharge ses messages et ses citations. La
réouverture de l'application retrouve les projets et les conversations persistés.
Les anciens échanges uniquement présents dans la mémoire d'une page ne sont pas
des conversations archivées récupérables après un rechargement.

## Suppression et erreurs

La suppression d'un projet demande une confirmation explicite dans l'interface,
en indiquant qu'elle supprime ses documents et ses conversations. Le serveur
refuse cette suppression lorsqu'une opération utilise ce projet. Supprimer toutes
ses collections, y compris celles des autres modèles d'embeddings utilisés.

Une erreur de nettoyage ne doit pas produire une suppression annoncée comme
réussie. Conserver un état permettant de réessayer le nettoyage et empêcher
l'utilisation d'un projet en cours de suppression. Ne toucher à aucune collection
d'un autre projet. Si le dernier projet est supprimé, afficher un état vide avec
une action de création ; ne pas recréer silencieusement le projet « Général ».

Les erreurs d'identifiant, de stockage ou d'import donnent un message exploitable
sans afficher les détails internes ou les secrets des fournisseurs.

## Vérifications d'acceptation

1. Créer deux projets A et B ; importer dans chacun un fichier portant le même
   nom mais contenant des textes distincts. La liste, la recherche vectorielle,
   BM25 et les citations de A ne contiennent aucune donnée de B, et inversement.
2. Créer plusieurs conversations dans A et une dans B. Vérifier leurs historiques
   indépendants, leur persistance et leur rattachement après réouverture.
3. Tenter de discuter dans A avec l'identifiant d'une conversation de B : rejet
   avant recherche, génération ou écriture d'un message.
4. Vérifier que l'historique utilisé par le serveur provient de la conversation
   stockée, sans pouvoir être remplacé par des messages arbitraires du navigateur.
5. Supprimer un document puis un projet ; vérifier la conservation intégrale des
   documents et conversations du projet voisin.
6. Vérifier les réponses tardives, interruptions et erreurs de génération, ainsi
   que le refus d'une suppression concurrente à une opération en cours.
7. Vérifier que les collections historiques restent accessibles depuis « Général »
   et que les différents modèles d'embeddings conservent leur séparation.
8. Exécuter la suite de tests existante et les contrôles frontend disponibles.
   Distinguer les tests avec fournisseurs simulés d'une génération IA réellement
   exécutée sur le serveur configuré.

## Limites du périmètre

Pas de déplacement de fichiers entre projets, d'export, de collaboration, de
permissions configurables par projet ou de changement des fournisseurs IA. Conserver les
formats d'import, les paramètres des modèles et le comportement des citations.
