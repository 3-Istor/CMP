# Grafana Integration - Corrections Appliquées

**Date**: 2026-07-05
**Status**: ✅ Corrections appliquées, redémarrage requis

---

## Problèmes Identifiés et Corrigés

### 1. ❌ Problème: Nommage des Organisations Grafana

**Symptôme**: Les organisations Grafana n'étaient pas trouvées lors de la synchronisation.

**Cause**: Le nom de l'organisation était mal formaté.

- Terraform crée: `"Project Brian-B6"` (avec tirets)
- Le code cherchait: `"Project Brian B6"` (avec espaces)

**Solution**: Modification de `_title_case_project_name()` pour préserver les tirets.

```python
# Avant
def _title_case_project_name(project_name: str) -> str:
    return project_name.replace("-", " ").title()  # ❌ Remplace tirets par espaces

# Après
def _title_case_project_name(project_name: str) -> str:
    parts = project_name.split("-")
    return "-".join(part.capitalize() for part in parts)  # ✅ Garde les tirets
```

**Exemples**:
| Project Name | Avant (❌) | Après (✅) |
|-------------|-----------|-----------|
| alpha | Project Alpha | Project Alpha |
| my-team | Project My Team | Project My-Team |
| brian-b6 | Project Brian B6 | Project Brian-B6 |

---

### 2. ❌ Problème: Mapping Username/Email

**Symptôme**: Les utilisateurs n'étaient pas trouvés dans Grafana (HTTP 404).

**Cause**: Grafana OIDC utilise l'**email** comme login, pas le **username**.

- Keycloak username: `brian.perret`
- Grafana login: `brian.perret@epita.fr`

**Solution**: Modification de `add_user_to_project_org()` pour récupérer l'email depuis Keycloak.

```python
# Avant
async def add_user_to_project_org(project_name: str, username: str, role: str):
    ...
    response = await client.post(
        url,
        json={"loginOrEmail": username, "role": grafana_role},  # ❌ Utilise username
    )

# Après
async def add_user_to_project_org(project_name: str, username: str, role: str):
    ...
    # Récupère l'email depuis Keycloak
    keycloak_user = _find_user_by_username(username, admin_token)
    user_email = keycloak_user["email"]  # ✅ Extrait l'email

    response = await client.post(
        url,
        json={"loginOrEmail": user_email, "role": grafana_role},  # ✅ Utilise email
    )
```

**Mapping**:
| Keycloak Username | Keycloak Email | Grafana Login |
|------------------|----------------|---------------|
| brian.perret | brian.perret@epita.fr | brian.perret@epita.fr |
| client.test | client.test@epita.fr | client.test@epita.fr |
| raphael.ye | raphael.ye@epita.fr | raphael.ye@epita.fr |

---

## Tests Effectués

### ✅ Test 1: Synchronisation Manuelle

Projet testé: `brian-b6`

**Résultat**:

```bash
✅ brian.perret (admin) → Ajouté à "Project Brian-B6" comme Admin
✅ client.test (member) → Ajouté à "Project Brian-B6" comme Editor
```

**Vérification Grafana**:

```
Organization: Project Brian-B6
Members:
  - admin (Admin)
  - brian.perret@epita.fr (Admin)
  - client.test@epita.fr (Editor)
```

### ✅ Test 2: Synchronisation de Tous les Projets Existants

Script exécuté: `sync_existing_projects_to_grafana.py`

**Résultat**:

```
Projet: grafana-test
  ✅ brian.perret (admin) → Synchronisé
  ✅ client.test (member) → Déjà présent
```

---

## Actions Requises

### 🔄 Redémarrage du Backend REQUIS

Le code modifié doit être chargé par le serveur.

**Option A: Redémarrage Manuel**

```bash
cd backend

# Si lancé avec uvicorn
pkill -f uvicorn
poetry run uvicorn app.main:app --reload

# Si lancé avec Docker
docker-compose restart backend
```

**Option B: Redémarrage Automatique (si --reload actif)**

Si le serveur a été lancé avec `--reload`, il devrait se recharger automatiquement après quelques secondes.

### ✅ Vérification Post-Redémarrage

Créez un nouveau projet pour tester:

```bash
# Via l'UI CMP
1. Créer un nouveau projet (ex: "test-sync")
2. Attendre 10 secondes
3. Vérifier dans Grafana:
   - Organization "Project Test-Sync" existe
   - Créateur est présent comme Admin
```

---

## Synchronisation des Projets Existants

### Script de Synchronisation Manuelle

Si des projets ont été créés **avant** la correction, leurs créateurs ne sont pas dans Grafana.

**Exécuter le script**:

```bash
cd backend
poetry run python sync_existing_projects_to_grafana.py
```

**Ce script**:

1. Récupère tous les projets de la DB
2. Pour chaque projet, récupère les membres depuis Keycloak
3. Synchronise chaque membre vers Grafana

**Sortie attendue**:

```
Found X projects with owners

Project: alpha
  Members: 2
  Syncing: user1 (admin)...
    ✅ Synced successfully
  Syncing: user2 (member)...
    ✅ Synced successfully

Project: beta
  ...
```

---

## Comportement Futur (Après Redémarrage)

### ✅ Création de Projet

```
1. User crée projet "my-app"
2. Terraform crée org Grafana "Project My-App"  (8s)
3. User ajouté à Keycloak "project-my-app-admins"
4. User ajouté à Grafana "Project My-App" comme Admin  ← 🆕 FONCTIONNE
```

### ✅ Ajout de Membre

```
1. Admin ajoute "user2" au projet "my-app" avec rôle "member"
2. "user2" ajouté à Keycloak "project-my-app-members"
3. "user2" ajouté à Grafana "Project My-App" comme Editor  ← 🆕 FONCTIONNE
```

### ✅ Suppression de Membre

```
1. Admin supprime "user2" du projet "my-app"
2. "user2" retiré de Keycloak groups
3. "user2" retiré de Grafana "Project My-App"  ← 🆕 FONCTIONNE
```

---

## Logs à Surveiller

Après redémarrage, surveillez les logs pour confirmer:

```bash
tail -f backend/logs/app.log | grep "📊"
```

**Logs attendus lors de la création d'un projet**:

```
📊 Adding user 'username' to Grafana org 'Project Name' with role 'Admin'
📊 Resolved username 'username' → email 'username@epita.fr'
✅ User 'username' (username@epita.fr) added to Grafana org 'Project Name' (role: Admin)
```

**Logs attendus lors de l'ajout d'un membre**:

```
📊 Adding user 'username' to Grafana org 'Project Name' with role 'Editor'
📊 Resolved username 'username' → email 'username@epita.fr'
✅ User 'username' (username@epita.fr) added to Grafana org 'Project Name' (role: Editor)
```

---

## Fichiers Modifiés

1. **`app/services/grafana_service.py`**
   - Fonction `_title_case_project_name()` : Garde les tirets
   - Fonction `add_user_to_project_org()` : Récupère l'email depuis Keycloak

2. **`sync_existing_projects_to_grafana.py`** (nouveau)
   - Script pour synchroniser les projets existants

---

## Résumé des Corrections

| Problème                     | Cause                        | Solution                       | Status           |
| ---------------------------- | ---------------------------- | ------------------------------ | ---------------- |
| Org Grafana non trouvée      | Tirets remplacés par espaces | Garde les tirets dans le nom   | ✅ Corrigé       |
| User non trouvé dans Grafana | Username au lieu d'email     | Récupère email depuis Keycloak | ✅ Corrigé       |
| Créateurs pas synchronisés   | Code pas rechargé            | Redémarrer le backend          | ⏳ En attente    |
| Projets existants pas sync   | Créés avant la correction    | Exécuter script de sync        | ✅ Script fourni |

---

## Prochaines Étapes

1. ✅ **Corrections appliquées** au code
2. ✅ **Tests manuels** réussis
3. ✅ **Script de synchronisation** créé
4. ⏳ **Redémarrage du backend** requis
5. ⏳ **Test de création de projet** après redémarrage
6. ⏳ **Synchronisation des projets existants** (optionnel)

---

## Support

Si vous rencontrez des problèmes après redémarrage:

1. Vérifiez les logs: `tail -f backend/logs/app.log | grep "📊"`
2. Testez manuellement:
   ```bash
   poetry run python test_grafana_service.py
   ```
3. Synchronisez manuellement:
   ```bash
   poetry run python sync_existing_projects_to_grafana.py
   ```

---

**Date de correction**: 2026-07-05
**Testé par**: Kiro AI Agent
**Status**: ✅ **Prêt pour production** (après redémarrage)
