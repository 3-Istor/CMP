# CNP-170 — proposition UI/UX à faire valider

État du 2 novembre 2026. Ce document prépare le retour demandé par le PO ; la
refonte ci-dessous n’est pas implémentée sans son go. La correction fonctionnelle
OAuth remplace seulement les anciens liens Keycloak et permet la sélection d’une
installation réellement accessible.

## Retour sur l’existant

Dans `GitHubLinkButton.tsx`, l’utilisateur peut connecter GitHub, installer l’App
et sélectionner une installation. Dans `RepositoryImports.tsx`, il saisit ensuite
une URL et les paramètres de déploiement. Les droits sont bien contrôlés par le
backend ; ils ne doivent pas être déduits de l’état visuel du bouton.

Le parcours est compréhensible pour une personne connaissant GitHub Apps, mais
la navigation Compte → GitHub → Compte → Projet reste longue et l’état de liaison
n’est pas visible directement dans le formulaire. Le terme installation est
technique ; saisir à la main des champs détectables augmente les erreurs.
Le temps de liaison n’a pas été mesuré sur un vrai compte pendant ces tests.

## Proposition

1. Dans Projet → Importer, afficher d’abord « Connecter GitHub » si nécessaire.
   Expliquer en une phrase que le dev choisit ses repos ; conserver le projet
   et les champs saisis pendant l’aller-retour.
2. Un seul bouton principal pour connecter/installer l’App ; retour automatique
   au même formulaire. Si plusieurs comptes sont possibles, afficher leurs noms
   et avatars, jamais un champ installation_id brut.
3. Afficher uniquement les repos accessibles de l’installation sélectionnée,
   avec recherche. Permettre de modifier l’accès dans GitHub et de rafraîchir.
4. Préremplir la branche par défaut et proposer stack, dossier, port et santé
   détectés. Garder une section avancée pour les modifier ; le scan final doit
   toujours valider les valeurs réellement déployées.
5. Résumer le résultat : projet cible, repo/branche, fichiers proposés par PR,
   éventuels secrets requis, puis consentement explicite.
6. Afficher les étapes en cours et une action utile sur erreur : reconnecter,
   modifier l’accès aux repos, voir la PR, reprendre ou relancer. Une PR en attente
   ne doit jamais sembler encore en train de déployer.

## Critères proposés au PO

- Depuis une session CNP active, un utilisateur déjà connecté à GitHub relie son
  compte et choisit un repo en moins de 30 s dans le scénario nominal mesuré.
  Mesurer séparément login/MFA, choix d’organisation et validation GitHub imposés.
- Aucun passage par Keycloak pour GitHub ni identifiant d’installation à copier.
- Les champs du formulaire et le projet sont conservés après le retour GitHub.
- Les états non configuré, non connecté, repo absent, droits insuffisants, refus
  OAuth et expiration ont un message lisible et une action adaptée.
- Tous les chemins restent accessibles au clavier avec labels et focus cohérents.
- Les contrôles backend et le consentement restent obligatoires.

Fichiers concernés par une future refonte : `GitHubLinkButton.tsx`,
`RepositoryImports.tsx`, `api.ts`, routes bridge `/api/auth/github/link` et callback,
schémas de listing existants. Pas de modification du SSO, de l’App globale ou
d’Argo pour réaliser cette amélioration visuelle.
