# Déclenchement externe d'AutoPost (ne plus dépendre du seul planificateur GitHub)

## Pourquoi
Le planificateur de GitHub Actions ne garantit ni l'heure ni même l'exécution des tâches programmées :
du 16 au 20 novembre 2026, sur 24 exécutions prévues par jour, GitHub n'en a lancé que 3 à 11
(3 seulement le 20/11, d'où la publication manquée de 12:30). Un déclencheur externe appelle
directement l'API GitHub à heure fixe ; la programmation GitHub reste active en secours.

## Ce qui est indépendant, ce qui ne l'est pas
Le déclencheur externe remplace le **planificateur** de GitHub (la partie défaillante) : c'est lui qui décide de l'heure.
L'exécution elle-même (rendu, contrôles, envoi à Meta) reste sur les machines GitHub Actions, qui, elles,
démarrent immédiatement sur demande (vérifié le 20/11 : exécution lancée à la seconde après un envoi de code).

## Authentification
Jeton GitHub « fine-grained » limité à ce seul dépôt et à la seule permission « Actions : lecture/écriture » :
il ne peut que lancer des workflows existants, ni lire les secrets, ni modifier le code. Il est stocké
uniquement chez le déclencheur (cron-job.org ou secret Cloudflare), révocable à tout moment, expiration 1 an.

## Sans risque de doublon
- Toutes les exécutions passent par le même groupe de concurrence (`comptoir-state`) : jamais deux en parallèle.
- Chaque canal (Story Instagram, Post Instagram, Post Facebook) n'est publié que s'il n'est pas déjà
  « published » ou « uncertain » dans l'historique. Déclencher deux fois ne publie qu'une fois.
- Un créneau en retard de plus de 90 minutes n'est jamais publié automatiquement (alerte + rattrapage manuel).

## Solution retenue : cron-job.org (gratuit, sans abonnement)
1. Sur GitHub : Settings > Developer settings > Fine-grained personal access tokens > Generate.
   - Repository access : **uniquement** `comptoir-rotatif-autopost`.
   - Permissions : **Actions : Read and write** (rien d'autre). Expiration : 1 an (noter la date de renouvellement).
2. Sur https://cron-job.org (compte gratuit), créer ces tâches, fuseau **Europe/Paris** :

| Tâche | Heures | Workflow (`<fichier>`) |
|---|---|---|
| Post Instagram + Facebook | 09:31 et 09:50 | `due.yml` |
| Story Instagram | 16:31 et 16:50 | `due.yml` |
| Contrôle par canal | 10:20 et 17:20 | `controle.yml` |
| Planification | 20:20 | `weekly.yml` |

   Réglages de chaque tâche :
   - URL : `https://api.github.com/repos/aronattia13-cmyk/comptoir-rotatif-autopost/actions/workflows/<fichier>/dispatches`
   - Méthode : `POST`
   - En-têtes : `Authorization: Bearer <le jeton>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`
   - Corps : `{"ref":"main"}`
   - Notifications : activer l'e-mail en cas d'échec (réponse attendue : `204`).
3. Le jeton reste dans cron-job.org : il n'est jamais écrit dans le dépôt ni transmis à Claude.

## Test du jeton sans aucune publication
Avant la fusion, tester le raccordement avec `meta-check.yml` (« Meta - diagnostic (aucune publication) »),
corps `{"ref":"main"}` : réponse attendue `204`, puis une exécution apparaît dans l'onglet Actions.
`controle.yml` n'existe qu'après la fusion (avant : réponse 404, normal).
Tant que la branche n'est pas fusionnée, les appels à `due.yml` ne déclenchent que l'ancien calendrier
(créneaux 12:30 / 18:30 du lundi au vendredi) : rien n'est dû à 09:31, 09:50, 16:31 ni 16:50.

## Alternatives équivalentes
- Cloudflare Workers « Cron Triggers » (offre gratuite) : même appel HTTP, plus technique.
- Google Apps Script (déclencheur horaire, gratuit) : même appel HTTP depuis un compte Google.

## Alertes et preuves
- Après chaque publication, le contrôle par canal (`python -m comptoir.run check`) exige pour chaque canal
  un statut « published » + l'identifiant Meta, relu chez Meta, et le lien quand Meta le fournit
  (les stories n'ont pas toujours de lien public).
- Manque ou échec : exécution rouge (e-mail GitHub) + issue « AutoPost : publication manquée » (notification).
- Planification : un créneau sans contenu inédit et validé rend la planification rouge (alerte la veille).

## Rattrapage sécurisé
Actions > « Rattrapage d'une publication » > Run workflow :
identifiant du créneau (dans l'issue d'alerte), canal (instagram ou facebook), phrase `JE CONFIRME LE RATTRAPAGE`.
Refusé automatiquement si ce canal est déjà publié ou incertain, si une publication identique est déjà visible
chez Meta, si le créneau a plus de 2 jours, ou si un contrôle prix / photo / stock échoue.
