# UP/DOWN des clusters publics depuis le CMP — design

Tickets : CNP-109 (boutons UP/DOWN), CNP-128 (état des clusters, intégré ici). S'appuie sur CNP-129 (rôle `cnp-admin`).

## Intention

Dans le jeu de rôle, EKS et GKE sont considérés comme toujours UP. Un admin plateforme peut les arrêter pour des raisons FinOps, puis les relancer depuis le CMP, sans passer par `Cloud/terraform/public/{aws/EKS,gcp/GKE}/cnp.sh` en local. Le CMP devient la source de vérité de l'état de ces clusters et bloque les déploiements vers un cluster qui n'est pas UP.

## Objectifs

- Boutons UP et DOWN pour EKS et GKE, réservés à `cnp-admin`.
- Un état par cluster, visible par tout le monde sur l'accueil.
- Une progression par étapes, des logs en direct, un toast à la fin de l'opération.
- Un déploiement vers un cluster qui n'est pas `up` est refusé (CNP-128).
- Un retry automatique unique si le pod redémarre pendant une opération.

## Hors périmètre

- Planification automatique (heures creuses) et tout autre cluster que EKS et GKE.
- Notifications Discord : le toast dans le CMP suffit.
- File de tâches, workers séparés, Jobs Kubernetes : l'exécution se fait dans le pod backend (décision de Hugo).

## Anti-objectifs

- Un DOWN qui détruit le cluster sans prévenir l'admin des apps qui y tournent.
- Un utilisateur non admin qui peut déclencher une opération.
- Un pod redémarré qui laisse un lock S3 orphelin et un état `starting` ou `stopping` bloqué pour toujours.

## Décisions de Hugo

1. **Exécution dans le pod backend du CMP**, en réutilisant `TerraformExecutor` (sous-processus, logs écrits dans un fichier). Hugo accepte qu'une opération puisse être coupée par un redémarrage du pod (voir Retry).
2. **Le code Terraform est intégré à l'image au build** : la CI du CMP fait un checkout de `3-Istor/Cloud` et copie `terraform/public` (`aws/EKS`, `gcp/GKE`, `modules/`, et ce qu'ils référencent) dans l'image. On lance aussi `terraform init -backend=false` au build pour mettre les providers en cache. Conséquence : un changement dans `Cloud` n'est pris en compte qu'au rebuild du CMP.
3. **UP est terminé** quand ArgoCD a enregistré le cluster **et** que le pod WireGuard (`cnp-vpn`, `app=wireguard`) est Running. Les nodes Ready ne suffisent pas.
4. **Progression par étapes** : init → apply (ressources faites / total du plan) → kubeconfig → nodes Ready → enregistré dans ArgoCD → WireGuard Running. Pour DOWN : vérifications → destroy (ressources détruites / total).
5. **DOWN avec des apps déployées** : une modale liste les apps des projets dont `target_cloud` correspond à ce cloud, puis l'admin confirme. Les apps reviennent seules au UP suivant via ArgoCD (vérifié en test par l'équipe). Le refus de `cnp.sh` quand il reste des Services `LoadBalancer` est conservé.
6. **Retry automatique** une seule fois après un redémarrage du pod, si c'est peu coûteux (voir plus bas).
7. **Notification** : un toast `sonner` dans le CMP uniquement.
8. **Interface** : dans `GlobalInfraHealth` (carte « Infrastructure Health » de l'accueil), les colonnes « AWS VPNs » et « OpenStack VPN » sont **remplacées** par **EKS** et **GKE**. La colonne OpenStack Hypervisors ne change pas.
9. CNP-128 est intégré à ce chantier.
10. **Admins des clusters** : `admin_principal_arn` (EKS) devient une liste (changement Terraform dans `Cloud`), avec Hugo et l'identité IAM du CMP. Côté GKE, même logique : Hugo et le service account du CMP.
11. **Les credentials sont en code dans le plan** : un utilisateur IAM AWS, un service account GCP et une policy Vault (écriture sur `kvv2/data/argocd/clusters/*` et `auth/kubernetes-*/config`), écrits en Terraform. Hugo les applique, puis on branche les secrets via `helm/arcl-cmp`.
12. **Le CMP est le seul opérateur** : on n'utilise plus `cnp.sh up/down` en local. Une réconciliation légère dans le polling de santé vérifie si le cluster existe vraiment chez le cloud provider, et corrige un état `up`/`down` qui ne correspond plus à la réalité (jamais pendant une opération en cours).

## Choix de Claude (à relire)

- **tfvars** : `terraform.tfvars` est ignoré par git dans `Cloud`. Les valeurs non secrètes (`api_allowed_cidrs`, `admin_principal_arn`, `vpn_source_cidrs`, type de machine…) passent par des valeurs Helm, injectées en `TF_VAR_*` dans le sous-processus.
- **IP de sortie** : hypothèse, le CMP (K3s on-prem) sort par `176.186.130.237`, déjà présente dans les `api_allowed_cidrs` d'EKS et de GKE. À vérifier en premier pendant l'implémentation.
- **Lock S3 lors du retry** : le backend tourne avec une seule réplique (`replicaCount: 1`). On ne force la libération d'un lock que si son `Who` correspond à un pod backend du CMP différent du pod actuel. Sinon, l'état passe à `error`.
- **Logs** : on les écrit sur le PVC `/app/data` (et pas dans `/app/logs`, qui est un `emptyDir`), pour qu'ils survivent au retry.

## Fonctionnement

### État

Une table, une ligne par cluster (`aws`, `gcp`, les mêmes valeurs que `TargetCloud` et que les secrets de cluster ArgoCD). Colonnes : `state` ∈ {`up`, `down`, `starting`, `stopping`, `error`}, opération en cours (`up` ou `down`), étape courante, compteurs de ressources, date de début, auteur, nombre de tentatives, fichier de log.

- **Initialisation** (ligne absente) et réconciliation : à déduire de la réalité (cluster présent ou non chez le cloud provider) plutôt que fixée en dur.
- Une transition n'est acceptée que depuis un état stable : UP depuis `down` ou `error`, DOWN depuis `up` ou `error`. Sinon, 409. Le lock S3 reste le garde-fou final.

### Exécution

Les opérations appellent l'équivalent de `cnp.sh`, réécrit en Python dans le backend :
- **UP** : `terraform init` → `apply -auto-approve` → kubeconfig (AWS : paramètre SSM `/cnp/eks/kubeconfig` ; GCP : `gcloud container clusters get-credentials cnp-gke --zone europe-west9-a`) → attente des nodes Ready → attente de l'enregistrement ArgoCD → attente de WireGuard Running.
- **DOWN** : kubeconfig → refus s'il reste un Service `LoadBalancer` → `terraform destroy -auto-approve`.
- L'opération tourne en tâche de fond. Les lignes de log vont dans un fichier propre à l'opération, et un endpoint SSE le lit en continu, sur le modèle de `/deployments/{id}/logs/stream`.
- Le compteur de l'étape apply se lit dans la sortie Terraform : total depuis `Plan: N to add`, progression depuis les lignes `Creation complete` / `Destruction complete`.

### Outils et credentials (image backend)

- On ajoute aws CLI, gcloud avec `gke-gcloud-auth-plugin`, et kubectl. Terraform 1.11.4 est déjà présent.
- AWS : il faut des droits sur EKS, EC2/VPC, SSM et IAM du compte `947554757826`, ainsi que sur le bucket de state `istor-cnp-tfstate-757826` et le bucket `cnp-wireguard-endpoints-757826`. Les clés AWS actuelles du backend sont limitées au state et aux VPN.
- GCP : un service account sur le projet `project-94dabc15-3981-4317-8da`. Le backend n'a aujourd'hui aucun credential GCP.
- Vault : `VAULT_ADDR` et un token autorisé à écrire les chemins de `cluster-addons`.
- Les secrets passent par le chart Helm `helm/arcl-cmp`, sur le modèle des clés AWS existantes.

### Retry après redémarrage

Au démarrage du backend, une ligne en `starting` ou `stopping` signifie que l'opération a été interrompue. Si `attempts < 2` : on libère le lock S3 de l'ancienne exécution (`parse_state_lock` fournit l'ID et le détenteur ; on ne force que si le lock correspond à cette opération), on incrémente `attempts` et on relance la même opération. Terraform est idempotent, donc rejouer `apply` ou `destroy` est sans danger. Sinon, l'état passe à `error`.

### Blocage des déploiements (CNP-128)

`POST /deployments` (et tout autre chemin qui déploie sur un cloud public) refuse avec un message clair quand le cluster du `target_cloud` du projet n'est pas `up`. On-prem n'est pas concerné.

### API (forme indicative)

- `GET` état des clusters : inclus dans la réponse de santé globale (`get_global_health`, `routers/infra.py`) pour réutiliser le polling de 15 s du front.
- `POST /clusters/{cloud}/up`, `POST /clusters/{cloud}/down` : réservés à `cnp-admin` (réutiliser `is_cnp_admin`, à déplacer vers `services/authz.py`). `down` renvoie les apps concernées si l'appel n'est pas confirmé.
- `GET /clusters/{cloud}/logs/stream` : SSE, réservé à `cnp-admin`.
- Une entrée dans le journal d'audit existant (`services/audit.py`) à chaque UP ou DOWN.

### Interface

- Les cartes EKS et GKE suivent le style des hypervisors : badge d'état, et état de WireGuard quand le cluster est `up`.
- Pour `cnp-admin` uniquement : bouton UP (si `down` ou `error`) ou DOWN (si `up`).
- DOWN ouvre une modale de confirmation qui liste les apps concernées.
- Pendant une opération : barre d'étapes, dernière ligne de log, accès aux logs complets (SSE).
- Le toast de succès ou d'échec est déclenché par le changement d'état vu par le polling global. Il apparaît donc sur n'importe quelle page, pas seulement sur celle d'où l'opération a été lancée.

## Laissé au builder

- Le schéma exact de la table et la migration, ainsi que la façon de détecter l'état initial.
- Comment vérifier l'enregistrement ArgoCD : secret de cluster sur le K3s on-prem, ou `connectionState` via l'API ArgoCD.
- La forme des logs complets (panneau dépliable ou modale) et le placement exact de la barre.
- Retirer le code backend des VPN (`_get_aws_vpn_status`, `_get_openstack_vpn_status`) si plus rien ne s'en sert.
- Le découpage du Dockerfile et du workflow `build-and-push.yml` (token de lecture sur `Cloud`).
