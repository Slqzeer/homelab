# Plan de mise en place de la workstation Kubuntu / Homelab

## 1. Objectif général

L’objectif est de transformer la machine Kubuntu en une workstation de développement complète et en une plateforme homelab structurée autour de :

- `mise` pour la gestion des runtimes et versions de langages ;
- GitHub comme forge Git et source de vérité externe ;
- Docker pour le build, les tests et les usages locaux ;
- k3s comme plateforme Kubernetes principale ;
- Argo CD pour le GitOps ;
- Vault pour la gestion des secrets ;
- PostgreSQL et Redis selon les besoins ;
- GitHub Actions et GHCR pour la CI/CD et les images conteneur ;
- Artifactory éventuellement, pour des besoins avancés de gestion d’artefacts ;
- Prometheus, Grafana et éventuellement Loki pour l’observabilité ;
- un système de sauvegarde séparé sur HDD.

L’IA locale n’est pas prioritaire et sera ajoutée plus tard si nécessaire.

---

# 2. Répartition des disques

## SSD système — Kingston 256 Go

Montage principal :

```text
/
```

Utilisation :

```text
Kubuntu
applications système
VS Code
Git
mise
runtimes de développement
Docker Engine
k3s système
kubectl
Helm
outils CLI
~/Projects
```

Disposition logique :

```text
/
├── home/
│   └── <user>/
│       ├── Projects/
│       └── .local/share/mise/
├── etc/
├── usr/
└── var/
```

Le SSD système ne doit pas accueillir les données volumineuses des services Kubernetes.

---

## SSD données — Fanxiang 1 To

Montage :

```text
/srv
```

Utilisation :

```text
/srv/
├── kubernetes/
│   └── storage/
├── vm/
├── games/
├── staging/
└── ai/              # plus tard
```

### `/srv/kubernetes`

Contient les données persistantes Kubernetes.

```text
/srv/kubernetes/
└── storage/
```

Les PersistentVolumeClaims pourront être provisionnés sur ce SSD.

Exemples :

```text
Vault PVC
PostgreSQL PVC
Artifactory PVC
Grafana PVC
Prometheus PVC
applications personnelles
```

### `/srv/vm`

Contient les images et disques de machines virtuelles KVM/QEMU.

### `/srv/games`

Contient les données des serveurs de jeux.

Exemple :

```text
/srv/games/
├── ark/
├── minecraft/
├── valheim/
└── autres/
```

### `/srv/ai`

Réservé à l’IA locale pour plus tard.

---

## HDD sauvegardes — Seagate 1 To

Montage :

```text
/backups
```

Ce disque ne doit pas héberger de données utilisées directement par des services actifs.

Organisation :

```text
/backups/
├── kubernetes/
├── databases/
├── vault/
├── services/
├── games/
└── system/
```

Principe :

```text
/srv
→ données actives

/backups
→ copies restaurables
```

---

# 3. Ordre global recommandé

```text
01. Stockage
02. Mise à jour Kubuntu
03. Workstation de développement
04. Runtimes via mise
05. GitHub
06. KVM / libvirt
07. Docker
08. k3s
09. kubectl
10. Helm
11. Argo CD
12. GitOps GitHub → Argo CD
13. Ingress
14. DNS
15. cert-manager
16. Vault
17. Vault Secrets Operator
18. PostgreSQL
19. Redis si nécessaire
20. GitHub Actions + GHCR
21. Artifactory si utile
22. Prometheus
23. Grafana
24. Logging
25. Keycloak et authentification centralisée
26. Applications personnelles
27. Serveurs de jeux
28. Automatisation des sauvegardes
29. Sauvegarde externe supplémentaire
30. IA locale plus tard
```

---

# 4. Phase 1 — Finaliser le stockage

Objectif :

```text
/dev/sda → /
/dev/sdb → /srv
/dev/sdc → /backups
```

Créer les dossiers de base :

```bash
sudo mkdir -p   /srv/kubernetes/storage   /srv/vm   /srv/games   /srv/staging   /srv/ai
```

Créer les dossiers de sauvegarde :

```bash
sudo mkdir -p   /backups/kubernetes   /backups/databases   /backups/vault   /backups/services   /backups/games   /backups/system
```

Les montages doivent être persistants via `/etc/fstab`, de préférence avec les UUID.

Ne pas appliquer :

```bash
sudo chown -R $USER:$USER /srv
```

Les permissions doivent être définies service par service.

---

# 5. Phase 2 — Mise à jour de Kubuntu

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt autoremove -y
```

Installer uniquement les outils système de base :

```bash
sudo apt install -y   git   curl   wget   unzip   zip   ca-certificates   jq
```

Pour l’instant, ne pas installer :

```text
build-essential
gcc
g++
cmake
ninja
```

Ils pourront être ajoutés plus tard si un projet les nécessite.

---

# 6. Phase 3 — Workstation de développement

Créer le dossier des projets :

```bash
mkdir -p ~/Projects
```

Organisation :

```text
~/Projects/
├── homelab/
├── app-a/
├── app-b/
└── experiments/
```

---

# 7. Phase 4 — Gestion des runtimes avec mise

`mise` devient le gestionnaire principal des versions de langages et outils.

Architecture :

```text
mise
├── Node.js
├── pnpm
├── Python
├── uv
├── Java
├── Maven
├── Gradle
├── Rust
└── Go éventuellement
```

Installation :

```bash
curl https://mise.run | sh
```

Activation Bash :

```bash
echo 'eval "$(~/.local/bin/mise activate bash)"' >> ~/.bashrc
source ~/.bashrc
```

Vérification :

```bash
mise --version
mise doctor
```

Exemple de configuration spécifique à un projet :

```bash
cd ~/Projects/mon-projet

mise use node@22
mise use python@3.12
mise use java@21
```

Cela crée un fichier :

```text
mise.toml
```

Exemple :

```toml
[tools]
node = "22"
python = "3.12"
java = "21"
```

Les runtimes de développement restent sur le SSD système dans :

```text
~/.local/share/mise
```

---

# 8. Phase 5 — Git et GitHub

Configurer Git :

```bash
git config --global init.defaultBranch main
git config --global core.autocrlf input
```

Configurer ensuite :

```bash
git config --global user.name "Nom"
git config --global user.email "email@example.com"
```

Créer une clé SSH pour GitHub :

```bash
ssh-keygen -t ed25519 -C "github"
```

GitHub sera utilisé comme :

```text
forge Git
source de vérité GitOps
hébergement des projets
CI/CD
registry de conteneurs via GHCR
```

Gitea n’est pas nécessaire dans cette architecture.

---

# 9. Phase 6 — VS Code

VS Code est installé sur le SSD système.

Extensions à ajouter selon les besoins :

```text
GitHub Pull Requests
Docker
Kubernetes
YAML
Python
Java
ESLint
Prettier
rust-analyzer
HashiCorp Terraform
HashiCorp HCL
```

Éviter d’installer toutes les extensions inutilement.

---

# 10. Phase 7 — Virtualisation KVM / libvirt

Installer :

```bash
sudo apt install -y   qemu-kvm   libvirt-daemon-system   libvirt-clients   virt-manager   virt-viewer
```

Ajouter l’utilisateur aux groupes :

```bash
sudo usermod -aG libvirt "$USER"
sudo usermod -aG kvm "$USER"
```

Les images VM seront stockées sur :

```text
/srv/vm
```

Exemples :

```text
VM Windows
VM Linux de test
VM Kubernetes isolée si nécessaire
```

---

# 11. Phase 8 — Docker

Docker ne sera pas la plateforme principale d’hébergement.

Son rôle :

```text
build d’images
tests locaux
Dockerfiles
docker compose pour tests
environnements temporaires
CI locale
```

Le moteur Docker peut rester sur :

```text
/var/lib/docker
```

Les données importantes ne doivent pas dépendre du stockage interne Docker.

Docker doit rester un outil de développement et de build.

---

# 12. Phase 9 — Kubernetes avec k3s

k3s devient la plateforme principale du homelab.

Architecture :

```text
Kubuntu
└── k3s
    ├── workloads
    ├── services
    ├── ingress
    ├── secrets
    └── PersistentVolumes
```

Le stockage persistant doit utiliser :

```text
/srv/kubernetes/storage
```

et non le SSD système.

Architecture conceptuelle :

```text
Pod
 ↓
PVC
 ↓
StorageClass
 ↓
/srv/kubernetes/storage
```

---

# 13. Phase 10 — kubectl et Helm

Une fois k3s fonctionnel :

```bash
kubectl get nodes
kubectl get pods -A
kubectl get storageclass
```

Helm sera utilisé pour installer ou déclarer les composants standards :

```text
Argo CD
Vault
cert-manager
Prometheus
Grafana
etc.
```

Mais après la mise en place d’Argo CD, les installations doivent autant que possible être pilotées par Git.

---

# 14. Phase 11 — Repository GitHub `homelab`

Créer un repository GitHub :

```text
homelab
```

Organisation recommandée :

```text
homelab/
├── bootstrap/
│   ├── argocd/
│   └── namespaces/
│
├── infrastructure/
│   ├── storage/
│   ├── ingress/
│   ├── cert-manager/
│   └── networking/
│
├── platform/
│   ├── vault/
│   ├── databases/
│   ├── nexus/
│   └── registry/
│
├── observability/
│   ├── prometheus/
│   ├── grafana/
│   └── logging/
│
├── apps/
│
└── environments/
    └── homelab/
```

---

# 15. Phase 12 — Argo CD

Argo CD devient le moteur GitOps.

Flux :

```text
GitHub
  ↓
Argo CD
  ↓
k3s
```

Argo CD gère notamment :

```text
Deployments
StatefulSets
Services
Ingress
ConfigMaps
Helm charts
CRDs
operators
versions d’images
configuration Kubernetes
```

Argo CD ne remplace pas les sauvegardes.

---

# 16. Phase 13 — Réseau Kubernetes

Avant les services sensibles, mettre en place :

```text
Ingress
DNS
HTTPS
```

k3s inclut Traefik par défaut.

Il peut être conservé au début pour limiter le nombre de composants.

---

# 17. Phase 14 — DNS

Mettre en place des noms internes cohérents.

Exemples :

```text
vault.home.arpa
grafana.home.arpa
nexus.home.arpa
```

ou utiliser un sous-domaine réel contrôlé par l’utilisateur.

Éviter `.local`, réservé à mDNS.

---

# 18. Phase 15 — cert-manager

Installer `cert-manager` après le réseau et l’Ingress.

Objectif :

```text
certificats TLS automatiques
renouvellement
HTTPS interne/externe
```

---

# 19. Phase 16 — Vault

Vault devient le gestionnaire central des secrets.

Architecture :

```text
Kubernetes
└── Vault
    ├── StatefulSet
    └── PVC
        ↓
    /srv/kubernetes/storage
```

Vault contient notamment :

```text
mots de passe DB
API tokens
credentials
certificats
secrets applicatifs
```

Les clés nécessaires au bootstrap de Vault ne doivent pas être stockées en clair dans Git.

---

# 20. Phase 17 — Vault Secrets Operator

Architecture :

```text
Vault
 ↓
Vault Secrets Operator
 ↓
Kubernetes Secret
 ↓
Application
```

Argo CD versionne la configuration qui indique quel secret récupérer, mais pas la valeur du secret.

Exemple conceptuel :

```text
Git
→ VaultStaticSecret
→ Vault
→ Kubernetes Secret
```

---

# 21. Phase 18 — PostgreSQL

PostgreSQL sera l’un des premiers services persistants.

Architecture :

```text
PostgreSQL Pod
      ↓
     PVC
      ↓
/srv/kubernetes/storage
```

Les sauvegardes seront stockées sur :

```text
/backups/databases/postgresql
```

Privilégier des dumps cohérents :

```text
pg_dump
→ compression
→ /backups/databases/postgresql
```

Ne pas simplement copier un répertoire PostgreSQL actif.

---

# 22. Phase 19 — Redis

Redis peut être ajouté si nécessaire.

Deux cas :

```text
Redis comme cache
→ backup souvent inutile

Redis persistant
→ PVC + stratégie de backup
```

---

# 23. Phase 20 — GitHub Actions et GHCR

GitHub Actions peut servir à :

```text
tester le code
compiler
construire les images
publier les images
```

GHCR devient le registry principal dans un premier temps.

Flux :

```text
git push
 ↓
GitHub Actions
 ↓
tests
 ↓
docker build
 ↓
GHCR
 ↓
Argo CD
 ↓
k3s
```

Cela évite d’installer immédiatement un registry interne.

---

# 24. Phase 21 — Artifactory

**Réalisé avec Sonatype Nexus Repository Community Edition, pas avec
Artifactory.** Aucune édition gratuite de JFrog ne couvre la liste ci-dessous :
Artifactory OSS n’a pas Docker, et JCR n’a ni Maven, ni npm, ni PyPI. Nexus CE
couvre les deux rôles dans un seul déploiement. Voir
`docs/superpowers/specs/2026-09-17-nexus-repository-design.md`.

Périmètre livré : `raw-hosted` pour les artefacts de build versionnés, et
`docker-proxy` comme cache pull-through de Docker Hub que k3s utilise en
miroir. Les proxys Maven/npm/PyPI sont reportés — aucun consommateur
aujourd’hui. Les images de ce homelab restent sur GHCR (phase 20).

Artifactory est optionnel au début.

Il devient pertinent pour :

```text
proxy Maven
proxy npm
PyPI
Docker registry
repositories internes
promotion d’artefacts
gestion centralisée des dépendances
```

Architecture livrée :

```text
Nexus Repository CE (namespace artifacts)
├── H2 embarqué + blob store + configuration
│     ↓
│    PVC nexus-data (local-path)
│     ↓
│   /srv/kubernetes/storage
├── 8081  → UI et raw-hosted        → nexus.taildf6cd4.ts.net
└── 8082  → connecteur Docker       → nexus-docker.taildf6cd4.ts.net
                                    → NodePort 30082
                                         ↑
                    /etc/rancher/k3s/registries.yaml (hors Argo CD)
                                         ↑
                                    containerd
```

Pas de PostgreSQL : Community Edition s’arrête à 40 000 composants, bien en
dessous des 100 000 de H2, donc une base externe n’apporterait aucune marge.
Voir la spec, section 6.2.

Nexus reste relativement lourd — environ 1,22 Gio mesurés en régime établi,
pour une limite de 2,5 Gio — et doit être ajouté après les fondations.
Documentation opérationnelle : `platform/nexus/README.md`.

---

# 25. Phase 22 — Prometheus

**Livrée fusionnée avec la phase 23 (Grafana) dans une seule release Helm**,
`kube-prometheus-stack` — Grafana est un subchart de ce chart, donc
l'installer deux fois reviendrait à dupliquer Prometheus et l'operator.
Voir `docs/superpowers/specs/2026-09-18-monitoring-stack-design.md`,
décision P2.

Prometheus collecte, via cinq intégrations livrées chacune dans son propre
fichier sous `observability/monitoring/targets/`, chacune preuve par une
requête PromQL le 2026-09-19 :

| Cible | Requête | Résultat |
| --- | --- | --- |
| PostgreSQL | `pg_up` | `1` |
| Redis | `redis_up` | `1` |
| Vault | `vault_core_unsealed` | `1` |
| Traefik | `traefik_config_reloads_total` | `3` |
| Argo CD | `count(argocd_app_info)` | `15` |

18 cibles actives, toutes `up`. **Les scrapers du control-plane k3s
(kube-scheduler, kube-controller-manager, kube-proxy, etcd) sont
délibérément désactivés** — sur ce cluster mono-nœud ils tournent comme des
goroutines à l'intérieur d'un seul processus `k3s server`, liés à
`127.0.0.1` ; les activer est une modification host-root du service systemd
k3s, un hand-off opérateur volontairement non pris ici. C'est un écart
assumé par rapport à la liste "CPU / RAM / nodes / pods / Kubernetes /
applications / services" ci-dessus prise dans son sens le plus large : ce
que Prometheus scrape couvre nodes/pods/Kubernetes et cinq applications
nommées, pas le control-plane lui-même.

Données persistantes sur un PVC 20Gi `local-path`, donc bien sous
`/srv/kubernetes/storage`, avec rétention 15 jours plafonnée à 12GiB
(`retentionSize`, la vraie limite — le PVC ne peut jamais être agrandi,
`local-path` a `ALLOWVOLUMEEXPANSION: false`). Confirmé par un test réel :
le pod a été supprimé puis recréé, et une requête à l'horodatage
pré-redémarrage a retourné les mêmes séries — l'historique survit bien au
volume, il ne repart pas vide.

Aucune sauvegarde de l'historique de métriques (comme prévu ci-dessus) : une
perte du volume coûte l'historique et rien d'autre, tout le reste
(dashboards, règles, datasources) est reconstruit depuis git.

Il n'y a **pas d'Ingress pour Prometheus** : aucune authentification
n'existe sur ce endpoint, et la policy tailnet actuelle est une autorisation
`*` → `*` unique — l'exposer reviendrait à publier son API d'admin à tout
l'appareil du tailnet. On l'atteint par port-forward ; la commande exacte
est dans `observability/monitoring/README.md`.

Détails complets, mesures et décisions (P1–P18) dans la spec ci-dessus et
dans `observability/monitoring/README.md`.

---

# 26. Phase 23 — Grafana

Grafana fournit dashboards, datasources et visualisation — **pas
d'alerting** : Alertmanager est désactivé (décision P3, aucune destination
de notification n'existe encore ; les ~30 règles d'alerte par défaut du
chart continuent malgré tout à charger et à s'évaluer dans Prometheus, seule
la livraison est absente).

**La configuration est entièrement déclarative et versionnée dans ce dépôt,
au-delà de ce que la phrase ci-dessus visait** : Grafana tourne sans PVC
(`persistence.enabled: false`, décision P4) — `/var/lib/grafana` est un
`emptyDir`, donc **tout dashboard construit à la main dans l'UI est perdu au
redémarrage suivant**. Ce n'est pas un défaut mais le but : un PVC rendrait
le cluster vivant autoritaire sur git au lieu de l'inverse. Tout ce qui doit
survivre est une ConfigMap étiquetée `grafana_dashboard: "1"`, ramassée
automatiquement par le sidecar de découverte. Un redémarrage à froid prend
environ 4 minutes et reprovisionne les ~24 dashboards embarqués par le chart
en une seule fois — mesuré lors du redémarrage forcé du 2026-09-19, où ce
pic a provoqué un OOMKill avant que la limite mémoire ne soit corrigée
(`values.yaml`, 256Mi → 512Mi).

Aucun dashboard n'est ajouté par cette phase (décision P7) : les ~24
dashboards du chart couvrent déjà la liste "CPU / RAM / nodes / pods /
Kubernetes" ci-dessus. Publiée sur le tailnet à
<https://grafana.taildf6cd4.ts.net> ; l'identifiant admin vient de Vault via
VSO, jamais tapé dans l'UI.

Détails complets, mesures et décisions dans
`docs/superpowers/specs/2026-09-18-monitoring-stack-design.md` et
`observability/monitoring/README.md`.

---

# 27. Phase 24 — Logging

Livrée. Loki stocke les logs de conteneurs du cluster, collectés par un
DaemonSet Grafana Alloy, et interrogés depuis le Grafana de la phase 23.
L'architecture visée est donc complète :

```text
Prometheus → métriques
Loki       → logs
Grafana    → visualisation
```

**Alloy, pas Promtail** : le chart `grafana/promtail` est marqué `deprecated`
dans l'index Helm, sa dernière version réelle date de mai 2025, et Promtail a
atteint sa fin de vie le 2026-03-02. Le chart `loki-stack`, qui aurait livré
les deux en une seule release, est lui aussi déprécié et fige Loki en 2.9.3.

Loki tourne en mode `SingleBinary` — un seul processus, un seul pod — sur un
PVC 10Gi `local-path`, donc sous `/srv/kubernetes/storage`, avec un stockage
`filesystem` et une rétention de **31 jours**. Le mode par défaut du chart
(`SimpleScalable`, neuf pods) et ses deux caches memcached — dont un qui
réclame **8 GiB** à lui seul — sont désactivés : ce nœud n'a qu'environ 4 Gio
disponibles.

**La rétention demande trois clés, pas une.** `retention_period` seul ne
supprime rien, parce que le `compactor: {}` par défaut du chart n'exécute
jamais la rétention. C'est `compactor.retention_enabled` et
`delete_request_store` qui l'arment réellement.

**Loki n'a pas d'équivalent de `retentionSize`.** Contrairement à Prometheus,
dont le plafond en taille est décrit dans `observability/monitoring/values.yaml`
comme « la vraie limite », la seule borne ici est rétention × débit, et
`local-path` n'applique pas la capacité du PVC. Deux choses s'y substituent :
des plafonds d'ingestion qui bornent une catastrophe et non la croissance
ordinaire, et une `PrometheusRule` sur l'espace libre du volume — la première
de ce dépôt. Cette règle **ne notifie personne** : Alertmanager reste
désactivé depuis la phase 22, elle passe au rouge dans l'UI de Prometheus et
nulle part ailleurs.

Le collecteur conserve exactement **quatre labels** — `namespace`, `pod`,
`container`, `app` — et non les labels du pod : chaque combinaison est un flux
distinct, et une explosion de cardinalité est ce qui tue un petit Loki, sans
retour possible une fois écrite dans les blocs.

Loki et Alloy sont scrapés par Prometheus (intégrations 6 et 7). Ce n'est pas
décoratif : quand Alloy ne peut pas lire `/var/log/pods`, il démarre, passe
ses probes et se déclare *Healthy* tout en ne collectant rien.

Il n'y a **pas d'Ingress pour Loki**, pour la raison exacte déjà retenue pour
Prometheus : aucune authentification, une API qui inclut une surface de
suppression, et une policy tailnet toujours en `*` → `*`. On l'atteint par le
datasource Grafana, ou par port-forward.

Aucune sauvegarde des logs, conformément à la section 32 : perdre le volume
coûte 31 jours de logs et rien d'autre.

Détails complets, mesures et décisions (L1–L19) dans
`docs/superpowers/specs/2026-09-19-logging-stack-design.md` et
`observability/logging/README.md`.

---

# 28. Phase 25 — Keycloak et authentification centralisée

Infrastructure livrée ; **acceptation navigateur encore à valider**.
Keycloak 26.7.4 fournit l'identité OIDC du homelab, avec **Grafana
comme client de validation**. Son formulaire admin local reste activé ;
**Argo CD n'est délibérément pas encore un client**, pour que la récupération
du système de déploiement ne dépende pas du fournisseur d'identité.

Mesuré le **2026-09-20** : **602Mi** au repos, 91 minutes après le dernier
redémarrage (pod âgé de 116 minutes), **603Mi** de maximum observé sur
15 minutes (632242176 octets), sans attribution prouvée à une connexion.
La limite est passée de 768Mi à **896Mi** : cette observation représente
67,3 % de la limite, et les limites mémoire engagées du nœud totalisent
**13098Mi / 82 %**. Un redémarrage du pod avec une base déjà initialisée
atteint Ready en **21 secondes**, sur un budget startupProbe inchangé de
200 secondes. Ce n'est pas une mesure d'installation sur base vide.

Le dump du **2026-09-20 à 15:34:32 +0200** établit **zéro utilisateur dans
`homelab`, un dans `master`**. Les connexions OIDC précédemment rapportées
par l'opérateur ne constituent pas une acceptation vérifiée. Restent à
rejouer : rôles Grafana Admin et Viewer, remplacement du mot de passe
temporaire, inscription et challenge TOTP, puis connexion admin locale.

Un Deployment sans PVC rejoint la vague 24, après PostgreSQL en vague 23.
Realms, clients, utilisateurs et graines TOTP résident dans la base
`keycloak`, sur le PVC PostgreSQL sous `/srv/kubernetes/storage`. Le nombre
de realms est resté à **2 après suppression du pod**, preuve de cette
persistance. L'accès est HTTPS, uniquement sur le tailnet, à
`https://keycloak.taildf6cd4.ts.net` ; Prometheus le scrape comme intégration 8.

**Le realm est reproductible depuis git, mais pas réconcilié.** Le seed
déclare `homelab`, les groupes `homelab-admins` et `homelab-users`, les
scopes et le client Grafana, sans utilisateur ni secret. `--import-realm`
utilise `IGNORE_EXISTING` : modifier le fichier ne change plus un realm
déjà importé, et une modification en console ne revient jamais dans git.
`keycloak-config-cli` a été écarté parce que son dernier build ciblait
26.5.5 face au serveur 26.7.4 ; ce décalage de deux versions mineures
ajoutait une dépendance risquée au service d'identité. L'export manuel
conserve les changements de configuration effectués en console.

**La MFA est obligatoire pour tout le realm**, via `CONFIGURE_TOTP`, et
pas seulement pour les administrateurs comme demandé initialement ici.
Le seed active aussi `UPDATE_PASSWORD`, non par défaut : déclarer
`requiredActions` supprime l'enregistrement automatique des actions
standard. L'opérateur doit l'enregistrer et l'activer dans le realm
existant avant l'onboarding ; `IGNORE_EXISTING` empêche le seed de le
réparer. La procédure exacte est dans `platform/keycloak/README.md`.
Chaque utilisateur doit aussi avoir une adresse email : sans elle Grafana
refuse la connexion avec `user email is not found`. Les groupes OIDC sont
traduits explicitement en permissions locales : `homelab-admins` devient
Admin d'organisation dans Grafana, les autres comptes Viewer.

Le routage OIDC sépare le front-channel du back-channel : le navigateur
utilise l'URL HTTPS tailnet pour l'autorisation, tandis que Grafana appelle
les endpoints token et userinfo via le Service interne
`keycloak.keycloak.svc.cluster.local:8080`. CoreDNS ne résout pas le nom
MagicDNS Tailscale depuis les pods ; l'utiliser pour le back-channel fait
échouer l'échange du code avant même la validation du secret client.

**`resetPasswordAllowed` est false, car il n'y a pas de SMTP** — un cas que
le plan initial n'avait pas anticipé. `master` ne contient qu'un admin de
récupération, sans TOTP : exiger l'appareil perdu pour récupérer le compte
annulerait ce recours. Son mot de passe généré de 32 caractères est conservé
dans Vault et livré par VSO ; ce compte n'est jamais utilisé au quotidien.
La procédure de récupération est dans `platform/keycloak/README.md`.

Les credentials PostgreSQL, de bootstrap et OIDC transitent par Vault/VSO,
jamais en clair dans git. Le secret client Grafana est émis par Keycloak et
doit être collé dans Vault lors du rebuild. Deux rotations restent manuelles :
changer le mot de passe admin dans Keycloak avant Vault, et reporter dans
Vault toute régénération du secret Grafana avant de redémarrer Grafana.

Les sauvegardes manuelles sont dans **`/backups/services/keycloak`** :
dump PostgreSQL du 2026-09-20 de **65175 octets**, gzip valide, et export
du realm de **66050 octets**, JSON valide avec `realm: homelab`.
Le répertoire est en mode **700**, les fichiers en **600** ; ils contiennent
des données sensibles, dont hashes et graines TOTP dans la base. L'export
contraint en mémoire écrit son fichier puis sort avec le code 1 sur un conflit
du port de gestion 9000 avec le serveur actif : le README documente la
validation du JSON avant publication atomique. Les nouvelles recettes
préservent les sauvegardes existantes en cas d'échec et exportent les
utilisateurs avec `--users realm_file`. De nouvelles sauvegardes sont
requises après onboarding : les artefacts historiques ne couvrent pas les
comptes créés ensuite. **Une restauration n'a pas été testée.**

L'arrivée du premier consommateur a aussi fermé l'ingress du namespace
`databases` par cinq NetworkPolicies. Validation réelle : `pg_up=1`,
`redis_up=1`, pod non autorisé refusé et les deux hooks Sync exécutés avec
succès. Les autres namespaces, sauf `argocd` déjà couvert par son chart,
restent ouverts, y compris `keycloak`.

Détails et cérémonies dans `platform/keycloak/README.md` et
`docs/superpowers/specs/2026-09-20-keycloak-design.md`.

---

# 29. Phase 26 — Applications personnelles

Une fois la plateforme stable, ajouter :

```text
Nextcloud
Vaultwarden
Immich
applications perso
autres services
```

Chaque application doit être gérée via GitOps autant que possible.

Lorsqu’elle le permet, elle doit utiliser Keycloak comme fournisseur OIDC afin
que les utilisateurs se connectent avec leur compte homelab unique.

---

# 30. Phase 27 — Serveurs de jeux

Stockage :

```text
/srv/games
```

Ils ne sont pas obligatoirement à placer dans Kubernetes.

Un serveur de jeu peut être plus simple avec :

```text
systemd
Docker
ou Kubernetes selon le cas
```

Décider au cas par cas.

---

# 31. Sauvegardes

Le HDD `/backups` est réservé aux sauvegardes.

Structure :

```text
/backups/
├── kubernetes/
│   ├── k3s/
│   └── etcd/
│
├── vault/
│   └── snapshots/
│
├── databases/
│   ├── postgresql/
│   └── autres/
│
├── services/
│   ├── nexus/
│   ├── keycloak/
│   └── autres/
│
├── games/
│
└── system/
    ├── fstab
    ├── networking
    └── configurations-importantes/
```

---

# 32. Ce qu’il faut sauvegarder

## Kubernetes

GitHub contient déjà :

```text
manifests
charts
versions
configuration déclarative
```

Sauvegarder en plus :

```text
état k3s / etcd
PVC importants
```

---

## Vault

Sauvegarder :

```text
snapshots Vault
configuration nécessaire à la restauration
éléments de bootstrap hors cluster
```

Destination :

```text
/backups/vault
```

---

## PostgreSQL

Sauvegarder via :

```text
pg_dump
```

Destination :

```text
/backups/databases/postgresql
```

---

## Nexus Repository (rôle Artifactory)

Sauvegarder :

```text
export de la base (tâche « Export databases for backup »)
blob store
```

Destination :

```text
/backups/services/nexus
```

**Copier la base H2 à chaud n’est pas une sauvegarde : elle se restaure
corrompue.** Il faut d’abord lancer la tâche d’export de Nexus, puis copier
`/nexus-data/backup/` **et** `/nexus-data/blobs/` — l’export sans les blobs
restaure un index qui ne pointe sur rien. La phase 21 n’automatise rien de
tout cela et n’a effectué aucun test de restauration ; c’est le travail de la
phase 34. Procédure détaillée : `platform/nexus/README.md`.

---

## Jeux

Sauvegarder :

```text
mondes
sauvegardes
configs
mods importants
```

Destination :

```text
/backups/games
```

---

# 33. Ce qu’il ne faut généralement pas sauvegarder

Ne pas sauvegarder inutilement :

```text
Pods
images Docker téléchargées
cache Docker
conteneurs éphémères
cache de build
```

Ces éléments doivent pouvoir être reconstruits.

Ce qui est réellement précieux :

```text
Git
Secrets
bases de données
PVC
Vault
données utilisateurs
configurations non reproductibles
```

---

# 34. Automatisation des sauvegardes

À terme :

```text
CronJob Kubernetes
ou
systemd timer
```

pour déclencher les sauvegardes.

Exemple :

```text
PostgreSQL
↓
pg_dump
↓
compression
↓
/backups/databases/postgresql
```

Le disque `/backups` ne doit pas être accessible en écriture à tous les Pods.

Un service compromis ne doit pas pouvoir supprimer simultanément ses données et ses sauvegardes.

---

# 35. Sauvegarde externe

Le HDD interne n’est qu’une première couche.

Architecture cible :

```text
SSD /srv
   ↓
HDD /backups
   ↓
NAS / autre machine / cloud chiffré
```

Une deuxième copie externe protège contre :

```text
panne totale
vol
erreur humaine
surtension
incident physique
```

---

# 36. IA locale

L’IA locale n’est pas prioritaire.

Lorsqu’elle sera ajoutée :

```text
/srv/ai/
├── models/
├── ollama/
└── autres/
```

Elle ne doit pas conditionner la mise en place du reste de la plateforme.

---

# 37. Architecture finale

```text
GitHub
│
├── projets
├── GitHub Actions
├── GHCR
└── homelab
      ↓
    Argo CD
      ↓
     k3s
      │
      ├── ingress
      ├── cert-manager
      ├── Vault
      ├── Vault Secrets Operator
      ├── PostgreSQL
      ├── Redis
      ├── Nexus Repository (raw + docker proxy)
      ├── Prometheus
      ├── Grafana
      ├── Logging (Loki)
      ├── Keycloak (SSO/OIDC)
      └── applications
```

Stockage :

```text
SSD 256 Go
/
├── Kubuntu
├── workstation
├── mise
├── Git
├── VS Code
├── Docker
└── k3s système

SSD 1 To
/srv
├── kubernetes/storage
├── vm
├── games
├── staging
└── ai

HDD 1 To
/backups
├── kubernetes
├── databases
├── vault
├── services
├── games
└── system
```

---

# 38. Priorité d’installation

La priorité immédiate est :

```text
1. Finaliser /srv et /backups
2. Mettre Kubuntu à jour
3. Installer Git + outils système
4. Installer mise
5. Installer VS Code
6. Configurer GitHub
7. Installer les runtimes nécessaires
8. Installer KVM/libvirt
9. Installer Docker
10. Installer k3s
11. Configurer le stockage Kubernetes sur /srv
12. Installer kubectl et Helm
13. Créer le repository GitHub homelab
14. Installer Argo CD
15. Passer progressivement en GitOps
16. Ajouter réseau, DNS et certificats
17. Installer Vault
18. Installer Vault Secrets Operator
19. Ajouter PostgreSQL
20. Ajouter la CI/CD GitHub Actions + GHCR
21. Ajouter Artifactory si nécessaire
22. Ajouter Prometheus et Grafana
23. Ajouter le logging
24. Installer Keycloak et configurer l’authentification OIDC
25. Intégrer les applications personnelles à Keycloak
26. Automatiser les backups
27. Ajouter une sauvegarde externe
28. Ajouter les serveurs de jeux
29. Ajouter l’IA locale uniquement plus tard
```

Ce document sert de feuille de route générale. Chaque phase doit être validée avant de passer à la suivante.
