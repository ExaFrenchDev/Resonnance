# Resonance

Réseau de rencontre par affinité musicale. Compte avec email obligatoire, sélection de genres, écoute d'extraits 30 s, score de compatibilité, messagerie temps réel et appels audio/vidéo.

## Démarrer

```bash
pip install -r requirements.txt
cp .env.example .env      # renseigne SECRET_KEY et le SMTP
python app.py             # http://127.0.0.1:5000
```

Sans identifiants SMTP, les codes de confirmation s'affichent dans la console (`[mail:console]`). Pratique en développement, à remplacer avant la mise en ligne.

Pour Gmail : active la validation en deux étapes puis génère un **mot de passe d'application** et mets-le dans `SMTP_PASSWORD`.

## Arborescence

```
app.py                  fabrique Flask + Socket.IO, gestion d'erreurs, en-têtes de sécurité
config.py               toute la configuration, lue depuis .env

modules/
  database.py           schéma SQLite, connexions thread-safe, helpers de requête
  auth.py               validation, inscription, codes email, session, décorateurs d'accès,
                        changement de mot de passe, suppression de compte
  mailer.py             SMTP, gabarits HTML, envoi asynchrone, diffusion d'annonces
  music_api.py          client Deezer avec cache mémoire (genres, charts, recherche, extraits)
  matching.py           vecteurs de profil, cosinus sur genres, recouvrement pondéré IDF
  messaging.py          conversations, historique, règle de déverrouillage, journal d'appels
  realtime.py           événements Socket.IO : présence, messages, frappe, signalisation WebRTC

routes/
  auth_routes.py        /inscription /confirmation /connexion /parametres + API compte
  music_routes.py       /gouts/genres /gouts/morceaux + API feed, recherche, ajout
  match_routes.py       /decouvrir /profil/<pseudo> + API matches, like, pass
  chat_routes.py        /messages /messages/<id> + API conversations

static/css/app.css      système de design complet
static/js/core.js       requêtes, toasts, lecteur d'extraits, spectre partagé SVG
static/js/discover.js   chargement des matchs, likes, ouverture de discussion
static/js/chat.js       chat temps réel + WebRTC (offre/réponse/ICE, micro, caméra)

templates/              base, shell (app connectée), plain (onboarding), et les 10 pages
```

## Le score

Trois axes, pondérés dans `config.py` :

| Axe | Poids | Méthode |
|---|---|---|
| Genres | 45 % | cosinus entre vecteurs de genres (déclarés + déduits des morceaux) |
| Artistes | 35 % | recouvrement pondéré par IDF — un artiste rare compte plus |
| Morceaux | 20 % | recouvrement exact des titres |

Le résultat passe par une courbe `x^0.72` pour étaler le haut de l'échelle, puis est converti en pourcentage.

Le score classe les profils mais n'ouvre jamais une discussion : **il faut un like des deux côtés**, quel que soit le pourcentage. `STRONG_MATCH` (70 % par défaut) sert seulement à repérer les affinités fortes, et `MATCH_ALERT_THRESHOLD` déclenche l'email d'alerte sur un like réciproque à fort score.

## Identité visuelle

Direction risographe : fond papier, deux encres — orange `#FF4A1C` pour toi, bleu `#2438C8` pour l'autre — et la surimpression `#2B0B18` là où les deux se superposent. Anton pour les titres, Archivo pour le texte, DM Mono pour les données.

L'élément signature est le **spectre partagé** : un graphique en miroir où chaque colonne est un genre, les barres du haut ton profil, celles du bas le sien, et le bloc foncé au centre la part commune. Il affiche de vraies données (`matching._spectrum`), pas une décoration.

## API musicale

Deezer, endpoints publics, sans clé ni quota d'authentification :

- `/genre` — la grille de genres de l'onboarding
- `/chart/{genre_id}/tracks` — les suggestions du feed
- `/search/track?q=` — la recherche
- chaque piste expose un `preview` : un MP3 de 30 secondes lu directement par le navigateur

Les morceaux sans extrait sont filtrés côté serveur. Les réponses sont mises en cache en mémoire (`CACHE_TTL`).

## Appels

WebRTC pair à pair, signalisation via Socket.IO (`call:invite`, `call:signal`, `call:accept`, `call:end`). Les serveurs STUN de Google sont configurés dans `Config.ICE_SERVERS`.

Deux limites à connaître :

- **HTTPS obligatoire** hors `localhost` — `getUserMedia` est bloqué en HTTP simple.
- **Un TURN est nécessaire en production.** STUN seul échoue quand les deux personnes sont derrière un NAT symétrique (4G, réseaux d'entreprise). Ajoute un serveur TURN (coturn auto-hébergé, ou un service type Twilio/Metered) dans `ICE_SERVERS`.

## Avant la mise en ligne

- `SECRET_KEY` aléatoire et `COOKIE_SECURE=1` derrière HTTPS
- `cors_allowed_origins` dans `app.py` : remplacer `"*"` par ton domaine
- passer sur `gunicorn` avec un worker `gevent` ou `eventlet` (le mode `threading` actuel convient au développement)
- SQLite tient jusqu'à quelques milliers de membres ; au-delà, `matching.candidates_for` recharge tous les profils à chaque appel — c'est le point à migrer en premier (PostgreSQL + cache Redis des vecteurs)
- ajouter un signalement et un blocage entre membres
- ajouter la récupération de mot de passe (`auth.issue_code` accepte déjà un `purpose`)
# Resonnance
# Resonnance
