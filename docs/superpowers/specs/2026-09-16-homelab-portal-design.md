# Portail applicatif du homelab — spécification

## 1. Objet

Créer un portail web, déployé dans le cluster k3s, qui présente les interfaces
web réellement exposées par Kubernetes et redirige vers elles. Il doit être
utile à la fois comme page d’accueil pour les utilisateurs du tailnet et comme
inventaire de diagnostic en lecture seule pour les administrateurs.

Le portail découvre les `Ingress` Kubernetes, mais la publication d’une entrée
reste une intention explicite et versionnée dans Git. Il ne crée, modifie ou
supprime jamais une ressource Kubernetes ou Git.

La première version prend en charge deux états de consultation :

- sans session : les seules applications marquées `public` ;
- avec une session Keycloak : les applications accordées à tout utilisateur
  authentifié, à ses groupes, ou au groupe d’administration.

## 2. Contexte du dépôt et contraintes

Le dépôt `homelab` est la source de vérité GitOps. Argo CD reconcilie les
manifests, et seul `environments/homelab/root.yaml` est appliqué manuellement.
Les objets `Application` vivent dans `environments/homelab/apps/`; un objet
placé dans le répertoire top-level `apps/` ne serait jamais synchronisé.

Les services HTTP actuels sont exposés exclusivement par les `Ingress`
Tailscale. Leurs manifests sont centralisés dans
`infrastructure/ingress/config/`, synchronisés au wave 21. Chaque Ingress
utilise un proxy dédié et l’annotation
`tailscale.com/proxy-class: homelab`; ce proxy consomme environ 28–30 MiB et
doit rester borné à 128 MiB par la `ProxyClass`. Le portail suit exactement ce
modèle : son Ingress est centralisé avec les autres, pas dans son dépôt
applicatif.

L’accès réseau Tailscale ne constitue pas une authentification applicative.
Keycloak est donc une dépendance préalable du portail : il est auto-hébergé
dans le cluster mais reste un sous-projet séparé, responsable de sa propre
persistance, sauvegarde, exposition, administration et disponibilité.

## 3. Objectifs mesurables

La version initiale est terminée lorsque :

1. une application exposée par un Ingress Tailscale et annotée est affichée
   avec son nom, sa description, sa catégorie et un lien HTTPS correct ;
2. une application non annotée n’est jamais exposée à un visiteur ;
3. un visiteur anonyme ne voit que les entrées `public` ;
4. un utilisateur connecté voit exactement l’union des entrées `public`,
   `authenticated` et de ses groupes ;
5. un membre du groupe Keycloak `portal-admin` peut consulter un inventaire
   en lecture seule des Ingress détectés, des entrées non publiées et des
   erreurs de métadonnées ;
6. la recherche, les filtres de catégories et de niveau d’accès restent
   utilisables au clavier et sur petit écran ;
7. la perte temporaire de Keycloak ou de l’accès à l’API Kubernetes ne donne
   jamais accès à une entrée non autorisée ;
8. le compte de service du portail ne peut lire que les Ingress nécessaires à
   son catalogue.

## 4. Périmètre

### Inclus

- Catalogue de redirection, recherche et filtres.
- Observation de tous les `networking.k8s.io/v1` `Ingress` du cluster.
- Publication contrôlée par annotations sur les Ingress Tailscale.
- Authentification OIDC avec Keycloak et autorisation par groupes.
- Vue de diagnostic réservée au groupe `portal-admin`, sans écriture.
- Déploiement GitOps du portail, de son RBAC, de son secret OIDC via Vault
  Secrets Operator (VSO), de son namespace, de son Application Argo CD et de
  son Ingress Tailscale.
- Journaux, métriques et probes nécessaires à l’exploitation.

### Explicitement exclu

- Installer, configurer, sauvegarder ou administrer Keycloak.
- Créer des Ingress, Services, DNS, certificats ou ACL Tailscale à partir du
  portail.
- Éditer les annotations, pousser dans GitHub, déclencher Argo CD ou modifier
  Kubernetes depuis l’interface.
- Déduire qu’un `Service`, Pod ou Deployment sans Ingress est une UI web.
- Découvrir ou rediriger des protocoles non HTTP(S), tels que PostgreSQL et
  Redis.
- Sondage actif des applications, vérification d’état distante, capture de
  favicon ou toute requête serveur vers les URL découvertes (pas de SSRF).
- Partage de liens publics Internet ou exposition LAN : le portail est
  accessible uniquement par son Ingress Tailscale.
- Gestion fine de rôles par application au-delà des groupes Keycloak.

## 5. Décisions d’architecture

| ID | Décision | Justification |
| --- | --- | --- |
| P1 | Découverte hybride : observation runtime + publication par annotation | Un catalogue manuel rate les nouveaux services, tandis qu’une publication automatique risquerait de divulguer des interfaces sensibles. |
| P2 | Seuls les Ingress de classe `tailscale` sont candidats en V1 | C’est le seul mode d’exposition supporté par le homelab actuel. |
| P3 | L’URL provient exclusivement du statut de l’Ingress | Le portail ne doit ni inventer un FQDN ni permettre une redirection arbitraire. |
| P4 | Une application est masquée par défaut | Une annotation incomplète ou oubliée ne peut pas devenir une fuite. |
| P5 | Keycloak + OIDC Authorization Code avec PKCE | Fournit une identité et un claim de groupes utilisable par l’application, indépendamment de l’accès réseau Tailscale. |
| P6 | Les droits sont évalués côté serveur | Les groupes et URLs non autorisés ne sont jamais rendus ni livrés au navigateur. |
| P7 | Un backend-for-frontend (BFF) unique sert l’UI et le catalogue | Évite une API publique séparée et conserve les jetons côté serveur/session HTTP-only. |
| P8 | Cache en mémoire piloté par `list/watch` | Pas de base de données ni de Redis requis pour un catalogue dérivé de Kubernetes. |
| P9 | RBAC cross-namespace minimal sur `Ingress` seulement | Le catalogue a besoin des métadonnées et du statut d’Ingress, jamais de Secrets, Pods ou Services. |
| P10 | Lecture seule, Git comme unique source de vérité | Toute écriture Kubernetes directe serait corrigée par Argo CD et contredirait le modèle GitOps. |

## 6. Architecture et flux

```text
                               ┌────────────────────────────┐
                               │ Keycloak (prérequis séparé) │
                               │ OIDC + claim de groupes     │
                               └──────────────┬─────────────┘
                                              │
Visiteur ── HTTPS/Tailscale ──┐               │ login OIDC
                              ▼               ▼
                         ┌──────────────────────────┐
                         │ Portal Deployment         │
                         │ UI + BFF + cache mémoire  │
                         │ sessions HTTP-only        │
                         └──────────────┬───────────┘
                                        │ get/list/watch seulement
                                        ▼
                         ┌──────────────────────────┐
                         │ Kubernetes API            │
                         │ Ingress + annotations     │
                         └──────────────┬───────────┘
                                        │
                                        ▼
                         Ingress Tailscale existants
                         (une carte = une redirection)
```

Le backend initialise le catalogue par un `list`, puis maintient un cache
local au moyen d’un `watch`. Les réponses de l’UI sont filtrées à chaque
requête selon l’identité de la session ; aucun cache de page ne doit être
partagé entre utilisateurs ayant des groupes différents.

Un Ingress ne devient une entrée candidate que si les conditions suivantes
sont toutes remplies :

1. `spec.ingressClassName` vaut `tailscale` ;
2. le statut contient exactement un hôte utilisable dans
   `status.loadBalancer.ingress[*].hostname` ;
3. il porte `portal.homelab.io/enabled: "true"` ;
4. ses annotations de catalogue sont valides.

Le statut est la source de l’URL : l’entrée pointe vers
`https://<hostname>`. Tant que le contrôleur Tailscale n’a pas renseigné ce
statut, l’Ingress reste diagnostiquable par un administrateur mais ne peut
pas être publié. Cela évite de supposer le suffixe MagicDNS ou de produire
une redirection erronée.

## 7. Contrat de métadonnées GitOps

Les annotations suivantes sont posées sur l’objet `Ingress`, dans son dépôt
ou son manifest propriétaire. Elles sont réconciliées par Argo CD comme toute
autre configuration.

```yaml
metadata:
  annotations:
    tailscale.com/proxy-class: homelab
    portal.homelab.io/enabled: "true"
    portal.homelab.io/name: "Vault"
    portal.homelab.io/description: "Gestion des secrets"
    portal.homelab.io/category: "Infrastructure"
    portal.homelab.io/icon: "vault"
    portal.homelab.io/access: "groups"
    portal.homelab.io/groups: "admins,homelab-users"
    portal.homelab.io/order: "20"
```

| Annotation | Requise | Valeurs / règle |
| --- | --- | --- |
| `enabled` | oui | Doit être exactement `"true"`; toute autre valeur masque l’entrée. |
| `name` | oui | Libellé humain non vide, maximum 80 caractères. |
| `description` | non | Texte brut, maximum 240 caractères; jamais rendu comme HTML. |
| `category` | non | Libellé de filtre, maximum 40 caractères; défaut `Autres`. |
| `icon` | non | Identifiant issu du catalogue local embarqué; une valeur inconnue utilise l’icône générique. Aucune URL d’icône n’est acceptée. |
| `access` | oui | `public`, `authenticated`, `groups` ou `admin`. |
| `groups` | conditionnel | Liste CSV de noms de groupes Keycloak, obligatoire et non vide uniquement pour `groups`; comparaison exacte, sans casse implicite. |
| `order` | non | Entier de 0 à 9999; défaut 1000. Tri secondaire par catégorie puis nom. |

Pour `public`, `authenticated` et `admin`, `groups` est interdit afin que la
configuration ne suggère pas une politique différente de celle réellement
appliquée. Une entrée invalide est exclue de toutes les vues utilisateur. Le
diagnostic admin indique la ressource, la règle fautive et la correction
attendue, sans afficher de secret.

## 8. Identité et autorisation

Keycloak expose un client confidentiel dédié au portail. Il doit autoriser
uniquement l’URL HTTPS du portail comme URI de redirection et fournir dans le
jeton ID ou d’accès un claim stable de groupes. Le nom exact du claim et
l’identifiant de client deviennent une configuration non secrète du portail;
ils doivent être fixés dans le contrat de livraison Keycloak avant le
déploiement du portail.

Le navigateur utilise le flux OIDC Authorization Code avec PKCE. Le BFF
échange le code et conserve les informations de session dans un cookie
`Secure`, `HttpOnly`, `SameSite=Lax`, signé et chiffré par une clé dédiée. Les
jetons bruts ne sont jamais accessibles au JavaScript de l’UI.

L’évaluation est déterministe :

| Accès demandé | Règle |
| --- | --- |
| `public` | toujours visible |
| `authenticated` | session OIDC valide requise |
| `groups` | session valide et intersection non vide entre groupes du jeton et `groups` de l’annotation |
| `admin` | session valide contenant le groupe Keycloak exact `portal-admin` |

La page `/admin` applique aussi l’exigence `portal-admin`. Elle expose les
Ingress candidats non publiés, les erreurs de schéma et le dernier état de
synchronisation du watcher, mais ne propose aucune mutation.

À expiration de session, les cartes non publiques disparaissent. Une panne de
Keycloak bloque les nouvelles connexions et le renouvellement; une session
existante reste valable seulement jusqu’à son expiration locale, puis revient
à la vue publique. Cette dégradation est volontairement restrictive.

## 9. Expérience utilisateur

La page d’accueil comporte :

- une recherche sur nom, description et catégorie ;
- des filtres de catégories ;
- des cartes de redirection affichant icône locale, nom, description et
  catégorie ;
- un bouton de connexion lorsque des entrées non publiques existent ;
- un bouton de déconnexion pour une session active ;
- un lien vers `/admin` seulement si l’utilisateur est `portal-admin`.

Les filtres s’appliquent uniquement au catalogue déjà autorisé. Ils ne
servent jamais à inférer une carte masquée. Les liens ouvrent l’URL HTTPS
dérivée de l’Ingress, avec un libellé clair; le comportement nouvel onglet ou
même onglet est défini par l’UI mais ne modifie pas la cible.

L’interface est responsive, navigable au clavier, possède des libellés et
indicateurs de focus accessibles, et ne dépend pas d’un CDN ou d’une police
externe. Les icônes sont embarquées dans l’image, ce qui évite fuite de
métadonnées et indisponibilité tierce.

## 10. Déploiement GitOps

L’application doit avoir son propre dépôt. Il contient son code, son
Dockerfile, ses tests et ses manifests (Deployment, ServiceAccount,
ClusterRole, ClusterRoleBinding, Service, ConfigMap et objets VSO). Les images
y sont référencées par un tag concret et immuable, jamais `latest`.

Le présent dépôt ajoute :

1. le namespace `portal` dans `bootstrap/namespaces/namespaces.yaml` ;
2. une `Application` Argo CD dans `environments/homelab/apps/` pointant vers
   le dépôt du portail et une révision explicitement choisie ;
3. un `portal-ingress.yaml` dans `infrastructure/ingress/config/`, avec
   `ingressClassName: tailscale`, `tailscale.com/proxy-class: homelab`, un
   backend sur le Service du portail et un hôte TLS court `portal` ;
4. les annotations de portail sur les Ingress déjà publiables (au minimum
   celles qui seront volontairement rendues publiques).

L’Application portail est au wave 23. Elle arrive après l’opérateur VSO (21)
et sa configuration (22), dont elle dépend pour les secrets. Elle partage ce
wave avec PostgreSQL et Redis sans dépendre d’eux. L’Ingress reste au wave 21
conformément à la règle du dépôt : il ne doit pas être déplacé avec
l’application, sinon sa santé pourrait rebloquer des waves antérieurs.

Le projet Keycloak doit fournir avant ce déploiement : un Keycloak sain, le
client OIDC du portail, le claim de groupes convenu, le groupe `portal-admin`,
les URI de redirection et le secret de client. Le secret de client et la clé
de session sont créés dans Vault et projetés dans `portal` par VSO; ils ne
doivent apparaître ni dans Git, ni dans une ligne de commande, ni dans un
ConfigMap.

Le Deployment a une seule réplique en V1, aucune PVC et un système de fichiers
racine en lecture seule. Il reçoit des requests/limits explicites, à mesurer
pendant l’implémentation. Une sonde de disponibilité doit vérifier le serveur
local et la disponibilité du cache d’Ingress, sans exiger que Keycloak ou les
applications cataloguées répondent.

## 11. Sécurité et fiabilité

Le `ClusterRole` se limite à :

```yaml
apiGroups: ["networking.k8s.io"]
resources: ["ingresses"]
verbs: ["get", "list", "watch"]
```

Il n’accorde aucun droit d’écriture et aucun droit sur `secrets`, `pods`,
`services`, `deployments`, `namespaces`, `events` ou les ressources Argo CD.
Le portail n’envoie aucune requête vers les cibles découvertes, ce qui exclut
les SSRF et fait de lui un catalogue, non un moniteur.

Une erreur du watcher Kubernetes conserve le dernier catalogue valide en
mémoire avec un statut dégradé pour les administrateurs. Au démarrage, si le
premier `list` échoue, seules les cartes publiques ne peuvent pas être
fabriquées à partir d’un cache absent : le portail répond alors indisponible
plutôt que vide, avec une sonde de readiness en échec. Il ne fabrique jamais
une entrée par défaut.

Les réponses de diagnostic ne doivent pas divulguer les annotations d’un
Ingress non concernées par le portail, les tokens OIDC, la clé de session, les
Secrets, ni des URL non publiées à un utilisateur non `portal-admin`.

## 12. Observabilité

Le portail journalise de manière structurée : démarrage/arrêt de watch,
nombre d’Ingress candidats, entrées publiées par niveau d’accès, annotations
invalides, connexions réussies/échouées sans token, et refus d’accès. Les
journaux excluent les cookies, codes OIDC, jetons et en-têtes
`Authorization`.

Les métriques incluent au minimum : état et âge du dernier événement de watch,
taille du catalogue, nombre d’erreurs de validation, succès/échecs de login,
et refus d’accès. Une erreur de configuration doit ainsi être visible sans
ouvrir une session d’administration.

## 13. Stratégie de test et critères de recette

Tests automatisés requis :

- unitaires sur le parsing/validation de toutes les annotations et la
  dérivation d’URL depuis le statut d’Ingress ;
- unitaires sur chaque combinaison de niveau d’accès et de groupes ;
- tests de contrat OIDC : émetteur, audience, expiration, signature et claim
  de groupes incorrects sont refusés ;
- tests d’intégration avec un faux API Kubernetes : ajout, modification,
  suppression et erreur de watch mettent le catalogue à jour sans fuite ;
- test d’intégration Keycloak/OIDC couvrant anonyme, utilisateur authentifié,
  membre d’un groupe, non-membre et `portal-admin` ;
- test RBAC démontrant que le ServiceAccount peut lister les Ingress mais pas
  lire un Secret ni modifier un Ingress ;
- test e2e dans le cluster démontrant le lien du portail vers un Ingress
  Tailscale publié et l’absence du lien pour un utilisateur non autorisé.

La mise en production n’est acceptable que si le proxy Tailscale du portail a
sa limite mémoire, l’Ingress reçoit son hostname dans le statut, les secrets
sont synchronisés via VSO, l’Application Argo CD est `Synced/Healthy`, et les
tests anonymes/authentifiés/admin ci-dessus réussissent.

## 14. Évolutions différées

- Synchronisation de rôles plus fine que les groupes Keycloak.
- Catalogue de routes provenant d’autres contrôleurs Ingress.
- Santé applicative, favoris personnels, télémétrie d’usage ou notifications.
- Interface capable de créer une pull request GitHub; elle devra être un
  projet séparé, avec modèle d’autorisation et audit propres.
- Multi-réplicas, stockage de session et haute disponibilité, qui ne sont pas
  nécessaires sur le cluster mono-nœud actuel.
