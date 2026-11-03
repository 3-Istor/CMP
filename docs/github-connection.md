# Connecter GitHub directement dans CMP

Le compte CNP continue à utiliser son authentification habituelle. La connexion
GitHub utilise OAuth de la GitHub App CNP-Portal, directement dans CMP ; aucun
fournisseur GitHub Keycloak n’est nécessaire.

## Parcours utilisateur

1. Se connecter à CNP, puis ouvrir **Compte → Connexion GitHub**.
2. Pour une nouvelle installation, cliquer **Installer CNP-Portal sur mon compte
   ou mon organisation** et choisir les dépôts autorisés. L’installation relève
   des droits GitHub du développeur sur ce compte ou cette organisation.
3. GitHub revient à CMP avec un état lié à la session. CMP lance ensuite une
   autorisation OAuth avec PKCE et un nouvel état. Un éventuel `installation_id`
   ou code du retour d’installation ne sert jamais de preuve d’identité.
4. Si l’App est déjà installée, cliquer directement **Connecter mon compte GitHub**.
5. Après autorisation, choisir explicitement l’installation accessible et cliquer
   **Utiliser cette installation**. Aucun ID d’installation de plateforme n’est
   choisi automatiquement.

Une nouvelle autorisation OAuth efface l’ancienne sélection d’installation pour
faire confirmer le compte ou l’organisation voulus. Une révocation permet de
recommencer la connexion. L’import reste soumis à ses propres contrôles et à son
activation explicite.

## Configuration de la GitHub App et de CMP

Dans la GitHub App utilisée par CMP, enregistrer la callback utilisateur :

- Local : `http://localhost:3000/api/auth/github/callback`.
- Déployé : `https://<hôte-CMP>/api/auth/github/callback`.

Ce chemin appartient au frontend Next, comme les autres routes `/api/auth`.
Le backend reçoit ensuite un POST authentifié par la session CNP. Les routes
d’authentification CNP et le routage Envoy existants sont conservés.

Pour permettre aux développeurs d’installer l’App sur leurs propres comptes ou
organisations, sa distribution doit autoriser les autres comptes (**public /
Any account**). Une App limitée au compte propriétaire reste installable seulement
sur celui-ci. Cette configuration globale n’a pas été modifiée par ce travail.

Si **Request user authorization (OAuth) during installation** est activé sur
l’App, GitHub utilise sa première callback enregistrée, même si l’installation
vient du CMP local. Ne pas remplacer la première callback de production par
localhost pour une recette. Le parcours local sûr consiste à installer l’App
d’abord dans GitHub, puis à utiliser **Connecter mon compte GitHub** dans CMP :
ce flux explicite transmet son `redirect_uri` localhost, qui doit être enregistré
parmi les callbacks autorisés, avant la sélection de l’installation vérifiée.

Le bouton d’installation intégré est utilisable lorsque la callback d’installation
de l’App correspond au CMP ciblé. S’il est proposé un retour automatique sans OAuth
pendant installation, sa Setup URL peut utiliser le même endpoint. L’état protège
le retour ; il ne permet pas de choisir une autre première callback sur GitHub.

Paramètres backend, dans les fichiers locaux ignorés ou le gestionnaire de
secrets de l’environnement :

| Paramètre | Usage |
| --- | --- |
| `GITHUB_OAUTH_CLIENT_ID` | Client ID OAuth de la GitHub App utilisée par CMP. |
| `GITHUB_OAUTH_CLIENT_SECRET` | Client secret de cette même App. |
| `GITHUB_OAUTH_REDIRECT_URI` | Callback frontend exacte indiquée ci-dessus. |
| `GITHUB_OAUTH_TOKEN_KEY` | Clé Fernet stable pour chiffrer les credentials au repos. |

La clé privée et l’App ID déjà utilisés par le service GitHub restent nécessaires
pour vérifier l’installation réelle et produire les jetons techniques limités.
Le compte ou l’organisation cible vient de la sélection de l’utilisateur.

`make import-setup` génère la clé Fernet seulement lors de la création d’un nouveau
fichier backend local. Il préserve tout fichier existant et refuse de générer une
nouvelle clé si la base locale possède déjà des credentials OAuth sans le fichier
de configuration original. Sauvegarder la clé avec les credentials de déploiement :
la remplacer rend les anciens jetons indéchiffrables. Ne pas publier cette clé,
le client secret ou les jetons dans Git, les logs ou les captures d’écran.

Le flux demande `offline_access`, pris en charge par les GitHub Apps pour obtenir
des jetons expirants. Un grant sans durée de vie et refresh token valides est
refusé. Les permissions sur les ressources restent celles de l’intersection entre
les droits de l’utilisateur et ceux de l’App ; ce paramètre ne donne pas de droit
supplémentaire sur les dépôts.

## Vérifications et persistance

- Le départ exige une session CNP authentifiée. Un état aléatoire, son empreinte,
  le UUID CNP, une empreinte de session navigateur et le vérificateur PKCE chiffré
  sont enregistrés pour dix minutes. Le cookie de liaison est HttpOnly et
  SameSite=Lax, avec Secure en HTTPS.
- Le retour vérifie la même identité CNP, le même navigateur et le même `sid`
  CNP lorsqu’il est présent. Une mise à jour SQL conditionnelle consomme l’état
  avant l’échange du code, y compris contre les callbacks concurrents.
- `GET /user` établit le véritable ID numérique et le login GitHub.
  `GET /user/installations` est entièrement paginé ; l’App et son installation
  réelle sont contrôlées séparément, avec refus des installations suspendues.
- Le développeur sélectionne son installation. Les listes de dépôts issues de
  `GET /user/installations/{id}/repositories` sont paginées ; le nom complet et
  les permissions courantes pull/push sont vérifiés avant toute utilisation.
- Les jetons d’accès et de renouvellement restent chiffrés dans
  `github_oauth_credentials`. Ils ne sont envoyés ni au navigateur ni au worker.
  Le worker reçoit seulement les jetons techniques d’installation limités au dépôt.
- Un renouvellement utilise une session SQL indépendante et une réservation
  conditionnelle par version. Il ne commit jamais les changements en attente du
  job import appelant. Un renouvellement concurrent ou une révocation arrête le
  contrôle ; aucune ancienne preuve ou durée de vie supposée ne remplace le refus.

Les vérifications utilisateur et installation sont refaites pendant le parcours
import, avec un jeton valide ou renouvelé. Un échec, une révocation ou un dépôt
hors de l’installation choisie bloque l’étape suivante. Les contrôles projet,
cluster, scans, PR, image, Terraform et Argo restent ceux du protocole d’import.

## Fichiers principaux

| Fichier | Rôle |
| --- | --- |
| `frontend/src/app/api/auth/github/link/route.ts` | Pont serveur authentifié, départ OAuth ou installation et cookie de session. |
| `frontend/src/app/api/auth/github/callback/route.ts` | Retour serveur ; enchaînement installation/OAuth, puis redirection vers Compte sans code ni jeton. |
| `frontend/src/components/account/GitHubLinkButton.tsx` | Connexion, installation explicite et sélection parmi les accès vérifiés. |
| `backend/app/services/github_account.py` | État, PKCE, chiffrement, refresh, identité et permissions utilisateur/installation. |
| `backend/app/routers/account.py`, `schemas/account.py` | API CNP authentifiée de départ, callback et sélection. L’ancienne route de sélection exige la même preuve OAuth. |
| `backend/app/models/user_github.py` | Credentials chiffrés, états à usage unique et sélection d’installation existante. |
| `backend/alembic/versions/b82d04c71a96_direct_github_oauth.py` | Migration additive des deux tables ; joint les têtes import et main sans modifier les migrations des collègues. |

## Validation et limites

Les tests utilisent des identités et jetons fictifs, une base SQL temporaire et des
réponses GitHub simulées. Ils couvrent états étrangers/expirés/rejoués, installation
puis OAuth, chiffrement, pagination, révocation, permissions et concurrence de
renouvellement. Les contrôles frontend valident le pont serveur et la compilation.
Ils ne constituent pas une connexion OAuth réelle sur l’environnement déployé.

Aucun secret local, flag d’activation, installation GitHub, SSO ou cluster n’a été
modifié pour valider ce code. Le callback, les credentials et le parcours réels
restent à configurer puis à tester dans l’environnement autorisé.

Références : [flux utilisateur GitHub App et PKCE](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app),
[renouvellement et expiration](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens),
[état sur l’URL d’installation](https://docs.github.com/en/apps/sharing-github-apps/sharing-your-github-app),
[limites de la Setup URL](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-setup-url).
