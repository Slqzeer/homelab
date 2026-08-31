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
25. Applications personnelles
26. Serveurs de jeux
27. Automatisation des sauvegardes
28. Sauvegarde externe supplémentaire
29. IA locale plus tard
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
│   ├── artifactory/
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
artifactory.home.arpa
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

Architecture :

```text
Artifactory
├── PostgreSQL
└── filestore
      ↓
     PVC
      ↓
/srv/kubernetes/storage
```

Artifactory est relativement lourd et doit être ajouté après les fondations.

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

# 28. Phase 25 — Applications personnelles

Une fois la plateforme stable, ajouter :

```text
Nextcloud
Vaultwarden
Immich
applications perso
autres services
```

Chaque application doit être gérée via GitOps autant que possible.

---

# 29. Phase 26 — Serveurs de jeux

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

# 30. Sauvegardes

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
│   ├── artifactory/
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

# 31. Ce qu’il faut sauvegarder

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

## Artifactory

Sauvegarder :

```text
database
filestore
configuration importante
```

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

# 32. Ce qu’il ne faut généralement pas sauvegarder

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

# 33. Automatisation des sauvegardes

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

# 34. Sauvegarde externe

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

# 35. IA locale

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

# 36. Architecture finale

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
      ├── Artifactory
      ├── Prometheus
      ├── Grafana
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

# 37. Priorité d’installation

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
23. Ajouter les applications
24. Automatiser les backups
25. Ajouter une sauvegarde externe
26. Ajouter les serveurs de jeux
27. Ajouter l’IA locale uniquement plus tard
```

Ce document sert de feuille de route générale. Chaque phase doit être validée avant de passer à la suivante.
