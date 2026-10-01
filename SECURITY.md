# Sécurité et publication

Ne versionnez jamais les mots de passe, leurs hashes bcrypt, les clés API, les
fichiers `.env`, les configurations privées ou les documents des utilisateurs.
Les données applicatives sont exclues dans leur totalité via `data/`.
Si vous choisissez des chemins de stockage externes ou une configuration
différente, assurez-vous aussi de leur exclusion avant un commit.

Créez votre configuration avec `python scripts/init_auth.py`. Il n’existe pas
de mot de passe par défaut. Ne mettez pas de secret dans les options JSON de
génération : ce champ est une configuration de modèle visible dans la page.

Pour vérifier une publication, contrôlez `git status --short`,
`git diff --cached` et `git ls-files`. Un scanner de secrets peut compléter ces
contrôles. Si un secret a déjà été publié, révoquez-le ou faites-le tourner
auprès du fournisseur ; supprimer un fichier ne retire pas le secret de
l’historique Git. Les anciennes sessions nécessitent aussi un redémarrage.

L’application est destinée à des utilisateurs autorisés. Ils peuvent configurer
les adresses LLM que le serveur contacte. Ne distribuez pas d’accès à des
utilisateurs non fiables ; limitez les sorties réseau si vous l’hébergez.
Une API distante reçoit le contenu envoyé pour la génération ou les embeddings.
Appliquez les règles de confidentialité de vos documents avant de la choisir.

Un serveur répondant à `/models` peut encore échouer au chargement d’un modèle
(mémoire insuffisante, poids incompatibles, etc.). Les erreurs HTTP présentées
aux utilisateurs ne contiennent pas les réponses détaillées des fournisseurs.
