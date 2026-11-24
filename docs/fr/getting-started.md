Version anglaise : [Getting started](../getting-started.md)

# Premiers pas : votre première heure avec Pépite

Ce guide vous mène de zéro à votre première séquence d'e-mails. Vous n'avez pas besoin d'être
développeur. Chaque étape indique où cliquer, avec les noms que vous voyez à l'écran.

Pépite fonctionne sur votre ordinateur. Vos prospects restent sur celui-ci.

## 1. Installer

Choisissez une option. Les détails complets sont dans [install.md](../install.md).

- **Application de bureau (le plus simple).** Téléchargez le fichier pour Windows, Mac ou Linux
  depuis la [dernière version](https://github.com/aymaneprojects/leadcommons/releases/latest).
  Ouvrez-le comme n'importe quelle autre application. Votre système peut signaler que l'application
  n'est pas signée : [desktop.md](../desktop.md) montre quoi cliquer.
- **Une ligne dans un terminal.** Sur Linux ou Mac :
  `curl -fsSL https://raw.githubusercontent.com/aymaneprojects/leadcommons/main/scripts/install.sh | sh`.
  Sous Windows, utilisez la ligne PowerShell indiquée dans [install.md](../install.md). Lancez
  ensuite `pepite serve`.
- **Docker ou votre propre serveur.** Lancez `docker compose up --build` dans le dossier du projet,
  ou suivez [server.md](../server.md) pour un serveur partagé avec une connexion.

## 2. Premier démarrage

- **Application de bureau :** ouvrez Pépite. Elle ouvre sa propre fenêtre.
- **Installation dans un terminal :** lancez `pepite serve`, puis ouvrez <http://127.0.0.1:8000>
  dans votre navigateur.

La page d'accueil, **« Vos premières étapes avec Pépite (Your first steps with Pépite) »**, liste
ce qu'il faut faire ensuite. Un petit échantillon d'entreprises est intégré, vous pouvez donc
chercher tout de suite. Il vous faudra toutefois télécharger les entreprises de votre pays.

## 3. Télécharger les entreprises de votre pays

L'application de bureau lance cette opération pour vous, pour le pays de votre ordinateur. Suivez-la
dans le menu **« Travaux (Jobs) »** en haut, qui affiche **« 1 en cours (1 running) »**. Pour choisir
d'autres pays :

1. Ouvrez **« Paramètres (Settings) »**, puis **« Packs de données (Data packs) »**.
2. Dans le tableau **« Pays (Countries) »**, cliquez sur **« Télécharger (Download) »** à côté d'un pays.
3. Attendez que le statut indique **« Installé (Installed) »**. Les grands pays prennent quelques
   minutes.

Dans un terminal, lancez `pepite pack download --country FR` (remplacez FR par le code de votre
pays). Lancez `pepite pack countries` pour voir la liste. Chaque mois, cliquez sur
**« Mettre à jour mes pays (Update my countries) »** sur la même page, ou lancez
`pepite pack download --update`.

## 4. Rechercher des entreprises

Ouvrez **« Entreprises (Companies) »** dans le menu latéral.

- **Avec des filtres.** Utilisez le panneau de filtres : **« Lieu (Location) »**,
  **« Nombre de salariés (# Employees) »**, **« Secteur et mots-clés (Industry & keywords) »** et
  d'autres. Cliquez sur **« Appliquer les filtres (Apply filters) »**. Les chiffres en haut,
  **Total**, **« Nouvelles (Net new) »** et **« Enregistrées (Saved) »**, se mettent à jour.
- **Avec une phrase simple.** Tapez ce que vous cherchez dans la zone
  **« Décrivez les entreprises que vous cherchez (Describe the companies you are looking for) »**,
  par exemple : « Entreprises de logistique en France de 50 à 200 salariés ». Cliquez ensuite sur
  **« Rechercher (Search) »**. Les filtres se remplissent pour vous. Cela fonctionne hors ligne. Pour
  utiliser votre propre modèle d'IA, voir les paramètres `PEPITE_LLM_URL` et `PEPITE_LLM_MODEL` dans
  [install.md](../install.md).

## 5. Ouvrir une entreprise et l'enregistrer

Cliquez sur le nom d'une entreprise. Un panneau s'ouvre avec ses détails et l'origine de chaque
information. Cliquez sur **« Enregistrer l'entreprise (Save company) »** pour la garder sur votre
ordinateur.

Pour en enregistrer plusieurs à la fois, cochez les cases des résultats, puis cliquez sur
**« Enregistrer (Save) »** dans la barre qui apparaît. Une fenêtre demande ce que vous voulez faire :

- **« Chercher des personnes et des coordonnées (Look for people and contact details) »** lit le site
  web de l'entreprise. Vous pouvez choisir **« Personnes uniquement (People only) »** ou
  **« Personnes et numéros de téléphone (People and phone numbers) »**. Les numéros de téléphone
  sont uniquement des lignes professionnelles.
- **« Ajouter à la liste (Add to list) »** place les entreprises dans une liste.

Cliquez sur **Enregistrer**. La progression s'affiche dans le menu des travaux, et vous pouvez
continuer à travailler.

## 6. Créer une liste et exporter un CSV

1. Cochez des lignes et cliquez sur **Ajouter à la liste** dans la barre. Choisissez une liste ou
   **« Nouvelle liste… (New list…) »**.
2. Ouvrez **Listes** dans le menu latéral pour voir toutes vos listes. Cliquez sur
   **« Créer une liste (Create list) »** pour en créer une vide.
3. Pour exporter, cochez des lignes et cliquez sur **« Exporter en CSV (Export CSV) »**, ou cliquez
   sur **Exporter en CSV** à côté d'une liste, sur la page **Listes**.

Les personnes de votre liste **« Ne pas contacter (do not contact) »** ne sont jamais exportées.

## 7. Trouver des personnes dans une entreprise

1. Ouvrez l'entreprise et cliquez sur **« Trouver plus de personnes (Find more people) »**. Ou cochez
   des entreprises dans les résultats, ouvrez le menu **« Plus d'actions (More actions) »** et
   choisissez **« Trouver des personnes (Find people) »**.
2. Pépite lit les pages publiques de l'entreprise et les registres officiels. Il n'utilise pas
   LinkedIn.
3. Ouvrez **Personnes** dans le menu latéral pour voir qui a été trouvé, avec leur fonction et leur
   source.

Toutes les entreprises ne publient pas de personnes sur leur site. Certaines n'en publient aucune.
C'est normal.

## 8. Trouver et vérifier un e-mail

1. Dans **Personnes**, ouvrez **Plus d'actions** sur la ligne d'une personne et choisissez
   **« Trouver l'e-mail (Find email) »**. Le bouton affiche **« Vérifier à nouveau l'e-mail
   (Re-check email) »** si une adresse a déjà été trouvée. Vous pouvez aussi cliquer sur
   **Trouver l'e-mail** sur la page de la personne.
2. Pépite apprend le format d'adresse de l'entreprise, puis interroge le serveur de messagerie.
   L'e-mail reçoit un statut : **Valide (Valid)**, **Catch-all**, **Inconnu (Unknown)** ou
   **Invalide (Invalid)**, ainsi qu'un score de 0 à 100.
3. N'envoyez qu'aux adresses **Valides** lorsque c'est possible. Un serveur **Catch-all** accepte
   n'importe quelle adresse : Pépite ne peut donc pas en être certain. [email-quality.md](../email-quality.md)
   explique jusqu'à quel point vous pouvez faire confiance au score.

Depuis un terminal : `pepite find-email "Ada Lovelace" example.com`.

## 9. Connecter une boîte mail

Vous envoyez les e-mails depuis votre propre boîte mail. Rien ne passe par un tiers.

1. Ouvrez **Paramètres**, puis **« Boîtes mail (Mailboxes) »** (la page s'appelle
   **« Envoi d'e-mails (Outbound email) »**). Cliquez sur **« Connecter une boîte mail (Connect mailbox) »**.
2. Choisissez votre fournisseur de messagerie dans la liste. Les champs du serveur se remplissent.
3. Saisissez votre nom, votre adresse e-mail et votre mot de passe. La plupart des fournisseurs
   demandent un **mot de passe d'application**, et non votre mot de passe habituel.
   [engage.md](../engage.md) explique comment faire.
4. Gardez **« E-mails par jour (Emails per day) »** bas au début (30 à 50). Vous pouvez aussi remplir
   **« Détection des réponses (IMAP) (Reply detection (IMAP)) »** pour que Pépite arrête la séquence
   dès que quelqu'un répond.
5. Cliquez sur **Connecter une boîte mail**.

Ouvrez ensuite l'onglet **« Règles d'envoi (Sending rules) »**. Saisissez votre adresse postale dans
**« Adresse postale (Postal address) »**. Si vous faites tourner Pépite sur un serveur, saisissez son
adresse web dans **« Adresse publique (Public address) »**. Chaque e-mail contient un lien de
désinscription, et **« Enregistrer les règles d'envoi (Save sending rules) »** garde vos choix.

## 10. Créer et lancer une séquence

Une séquence est une série d'e-mails (et de tâches) envoyés sur plusieurs jours.

1. Ouvrez **Séquences** et cliquez sur **« Créer une séquence (Create sequence) »**. Choisissez un
   modèle comme **« Introduction en trois messages (Three-touch introduction) »**, ou partez de
   **« Séquence vierge (Blank sequence) »**. Vous pouvez aussi essayer
   **« Rédiger avec l'IA (Draft with AI) »**.
2. Cliquez sur **« Ajouter une étape (Add a step) »** pour ajouter ou modifier des étapes. Chaque
   étape a un délai en jours. Utilisez `{{first_name|there}}` pour insérer un prénom.
3. Cliquez sur **« Ajouter des contacts (Add contacts) »**, ou cochez des personnes dans **Personnes**
   et choisissez **« Ajouter à une séquence (Add to a sequence) »**. Pépite ignore les personnes sans
   e-mail valide et celles de la liste **Ne pas contacter**.
4. Cliquez sur **Activer (Activate)**.

Pépite envoie les e-mails pendant qu'il tourne. Gardez l'application ouverte, ou lancez le serveur.
Dans l'application de bureau, la fermer arrête les envois. Voir [engage.md](../engage.md) pour les
plages d'envoi et l'expéditeur séparé (`pepite worker`).

## 11. Tâches et opportunités

**Tâches (Tasks)** regroupe les étapes que vous seul pouvez faire, comme les appels. Cliquez sur
**« Nouvelle tâche (New task) »** pour en ajouter une. **Opportunités (Deals)** est un tableau de vos
ventes en cours : cliquez sur **« Nouvelle opportunité (New deal) »**, puis glissez-la d'une étape à
l'autre. Les deux sont décrits dans [pipeline.md](../pipeline.md) et [engage.md](../engage.md).

## 12. Opposition et liste « Ne pas contacter »

Respectez le « non ». Pépite vérifie la liste partout : recherche, recherche d'e-mails, exports,
séquences.

- Pour ajouter quelqu'un vous-même, ouvrez **Paramètres**, puis
  **« Confidentialité et opposition (Privacy and opt-out) »**, saisissez une adresse e-mail ou un
  domaine et cliquez sur **« Exclure (Suppress) »**. Depuis un terminal :
  `pepite optout someone@example.com`.
- Sur la page d'une entreprise ou d'une personne, utilisez
  **« Marquer tout le monde dans cette entreprise (Mark everyone at this company) »** ou
  **« Marquer cette personne comme “ne pas contacter” (Mark this person as do not contact) »**.
- Les personnes peuvent aussi vous demander d'arrêter. Chaque e-mail contient un lien de
  désinscription, et la page publique **« Nous demander d'arrêter (Ask us to stop) »**
  (`/opt-out`) est accessible à tous.

Vous restez responsable du droit qui vous est applicable. Voir [privacy.md](../privacy.md).

## 13. Où sont vos données, et comment les sauvegarder

Tout se trouve dans un seul dossier : `.pepite`, dans votre dossier personnel. Dans l'application de
bureau, choisissez **« Aide (Help) »**, puis **« Ouvrir le dossier des données (Open the data folder) »**.

| Quoi | Où |
| --- | --- |
| Personnes, listes, séquences, opportunités, paramètres | `pepite.db` |
| Entreprises téléchargées | le dossier `packs` |
| La clé qui protège les mots de passe de votre boîte mail | `secret.key` |

Installé avant le changement de nom en Pépite ? Votre ancien dossier `.leadcommons` est toujours
utilisé.

**Pour sauvegarder :** fermez Pépite, puis copiez tout le dossier. Gardez `secret.key` avec
`pepite.db`. Sans la clé, vous devrez ressaisir les mots de passe de votre boîte mail. Le dossier
`packs` peut être re-téléchargé à tout moment : sa sauvegarde est donc facultative.

Sur un serveur avec Docker, sauvegardez la base de données comme indiqué dans [server.md](../server.md).

## 14. Mise à jour

- **Application de bureau :** elle vous prévient quand une nouvelle version existe. Vous pouvez aussi
  choisir **Aide**, puis **« Rechercher les mises à jour… (Check for updates…) »**. Téléchargez le
  nouvel installateur et lancez-le. Vos données restent en place.
- **Installation dans un terminal :** relancez la ligne d'installation.
- **Docker sur votre ordinateur :** récupérez le nouveau code, puis lancez `docker compose up --build`.
- **Serveur :** lancez `docker compose pull`, puis `docker compose up -d`.
- **Données des entreprises :** chaque mois, ouvrez **Paramètres**, **Packs de données**, puis
  cliquez sur **Mettre à jour mes pays**.

## Et ensuite

- Recherche et filtres : le [README](../../README.fr.md) contient des captures d'écran.
- Automatisations et webhooks : [workflows.md](../workflows.md).
- Utiliser Pépite depuis Claude ou ChatGPT : [mcp.md](../mcp.md).
- D'où viennent les données : [data-sources.md](../data-sources.md).
