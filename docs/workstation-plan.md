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
Artifactory OSS n'a pas Docker, et JCR n'a ni Maven, ni npm, ni PyPI. Nexus CE
couvre les deux rôles dans un seul déploiement. Voir
`docs/superpowers/specs/2026-09-17-nexus-repository-design.md`.

Périmètre livré : `raw-hosted` pour les artefacts de build versionnés, et
`docker-proxy` comme cache pull-through de Docker Hub que k3s utilise en
miroir. Les proxys Maven/npm/PyPI sont reportés — aucun consommateur
aujourd'hui. Les images de ce homelab restent sur GHCR (phase 20).

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

Pas de PostgreSQL : Community Edition s'arrête à 40 000 composants, bien en
dessous des 100 000 de H2, donc une base externe n'apporterait aucune marge.
Voir la spec, section 6.2.

Nexus reste relativement lourd — environ 1,25 Gio mesurés en régime établi,
pour une limite de 2,5 Gio — et doit être ajouté après les fondations.
Documentation opérationnelle : `platform/nexus/README.md`.

---

# 25. Phase 22 — Prometheus

Prometheus collecte :

```text
CPU
RAM
nodes
pods
Kubernetes
applications
services
```

Ses données persistantes peuvent être placées dans :

```text
/srv/kubernetes/storage
```

Il n’est généralement pas nécessaire de sauvegarder tout l’historique de métriques.

---

# 26. Phase 23 — Grafana

Grafana fournit :

```text
dashboards
datasources
alerting
visualisation
```

Une partie importante de la configuration devrait être déclarative et versionnée dans GitHub.

---

# 27. Phase 24 — Logging

Ajouter plus tard un système de logs, par exemple Loki.

Architecture :

```text
Prometheus → métriques
Loki       → logs
Grafana    → visualisation
```

---

# 28. Phase 25 — Keycloak et authentification centralisée

Keycloak devient le fournisseur central d’identité du homelab. Il fournit une
authentification unique (SSO) basée sur OpenID Connect (OIDC) afin que chaque
personne dispose d’un seul compte pour accéder aux applications compatibles.

Objectifs :

```text
un compte utilisateur par personne
une connexion unique pour toutes les applications
OIDC comme protocole d’intégration principal
groupes et rôles centralisés
désactivation d’un compte depuis un point central
```

Architecture :

```text
Utilisateur
    ↓
Keycloak
    ├── authentification
    ├── utilisateurs et groupes
    ├── rôles et permissions
    └── clients OIDC
          ↓
    Applications personnelles
```

Keycloak sera déployé dans Kubernetes et géré via GitOps. Sa base de données
doit être persistante, idéalement dans PostgreSQL, avec les données stockées
sur :

```text
/srv/kubernetes/storage
```

Pour chaque application compatible, créer un client OIDC dans le realm du
homelab avec des URL de redirection limitées au domaine de l’application,
les scopes minimaux nécessaires et un mapping explicite des groupes et rôles.

Les applications ne doivent pas gérer leur propre mot de passe lorsque
l’authentification OIDC est disponible. Elles délèguent la connexion à
Keycloak et utilisent les claims OIDC pour identifier l’utilisateur et
appliquer ses permissions.

Prévoir au minimum :

```text
realm dédié au homelab
groupes administrateurs et utilisateurs
MFA pour les comptes administrateurs
compte de récupération documenté et protégé
HTTPS obligatoire via l’Ingress
```

Les secrets OIDC, les credentials PostgreSQL et les clés de bootstrap ne
doivent pas être stockés en clair dans Git. Ils doivent être gérés avec Vault
et Vault Secrets Operator.

Sauvegarder la base de données Keycloak, la configuration des realms, clients,
groupes et rôles, ainsi que la procédure de récupération des comptes
administrateurs dans :

```text
/backups/services/keycloak
```

Keycloak centralise l’identité, mais ne remplace pas les autorisations propres
à chaque application. Chaque application doit traduire explicitement les
groupes ou rôles OIDC en permissions locales.

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

**Copier la base H2 à chaud n'est pas une sauvegarde : elle se restaure
corrompue.** Il faut d'abord lancer la tâche d'export de Nexus, puis copier
`/nexus-data/backup/` **et** `/nexus-data/blobs/` — l'export sans les blobs
restaure un index qui ne pointe sur rien. La phase 21 n'automatise rien de
tout cela et n'a effectué aucun test de restauration ; c'est le travail de la
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
