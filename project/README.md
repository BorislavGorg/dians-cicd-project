# Dians - Crypto Analytics Platform

Проект по предметот Континуирана интеграција и испорака (КИИИ).

## Архитектура

Апликацијата се состои од 3 сервиси и база на податоци:

- **main_app** (порт 5000) - Flask веб апликација (front-end + back-end) за приказ на крипто податоци, технички анализи и LSTM прогнози.
- **pipeline_service** (порт 5001) - микросервис кој повлекува дневни OHLCV/market cap податоци од CoinGecko API и ги запишува во базата.
- **technical_analysis_service** (порт 5002) - микросервис кој пресметува технички индикатори (SMA, RSI, MACD) и враќа BUY/SELL/HOLD сигнал.
- **db** - PostgreSQL база на податоци (табела `crypto_database`).

## Локално стартување (Docker Compose)

```bash
docker compose up --build
```

Апликацијата ќе биде достапна на http://localhost:5000

За да се повлечат податоци во базата, испратете POST барање до:

```bash
curl -X POST http://localhost:5000/run-fetch
```

## CI Pipeline (GitHub Actions)

Workflow-от се наоѓа во [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).

При секој **push на main** гранката, pipeline-от паралелно (со matrix стратегија)
ги гради трите Docker имиџи и ги објавува на Docker Hub:

- `<dockerhub-username>/dians-main-app`
- `<dockerhub-username>/dians-pipeline-service`
- `<dockerhub-username>/dians-technical-analysis-service`

Секој имиџ добива два тага: `latest` и краток git SHA (за следливост на верзиите).
При **pull request** имиџите само се градат (без push), што служи како проверка
дека кодот може успешно да се билдне.

### Потребни GitHub Secrets

Во репозиториумот, под *Settings → Secrets and variables → Actions*, треба да се додадат:

| Secret | Опис |
|---|---|
| `DOCKERHUB_USERNAME` | Docker Hub корисничко име |
| `DOCKERHUB_TOKEN` | Docker Hub access token (*Account Settings → Security → New Access Token*, со Read/Write дозволи) |

## Kubernetes

Сите манифести се наоѓаат во директориумот [`k8s/`](../k8s/), а ресурсите се
распоредуваат во посебен namespace **`dians-crypto`**:

```
k8s/
├── 00-namespace.yaml            # Namespace dians-crypto
├── db/
│   ├── db-configmap.yaml        # POSTGRES_DB, PGDATA
│   ├── db-secret.yaml           # POSTGRES_USER, POSTGRES_PASSWORD
│   ├── db-service.yaml          # Headless Service "db"
│   └── db-statefulset.yaml      # StatefulSet со volumeClaimTemplates (1Gi)
├── app/
│   ├── app-configmap.yaml       # URL-а на микросервисите
│   ├── app-secret.yaml          # DATABASE_URL (конекциски string кон базата)
│   ├── main-app-deployment.yaml
│   ├── main-app-service.yaml
│   ├── pipeline-deployment.yaml
│   ├── pipeline-service.yaml
│   ├── tech-analysis-deployment.yaml
│   └── tech-analysis-service.yaml
└── ingress.yaml                 # Ingress (nginx) на host dians.local
```

> **Напомена:** Во deployment манифестите имиџите се референцирани како
> `borislavgorg/dians-*`. Доколку вашето Docker Hub корисничко име е поинакво,
> заменете го во трите deployment датотеки.

### Поставување на кластер

Потребен е Kubernetes кластер (Docker Desktop Kubernetes, minikube или kind)
и инсталиран NGINX Ingress Controller:

```bash
# NGINX Ingress Controller (за Docker Desktop / kind)
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml

# За minikube наместо тоа:
# minikube addons enable ingress

# Апликација на сите манифести
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/db/
kubectl apply -f k8s/app/
kubectl apply -f k8s/ingress.yaml
```

Во `C:\Windows\System32\drivers\etc\hosts` (или `/etc/hosts` на Linux/Mac)
додадете:

```
127.0.0.1 dians.local
```

(за minikube користете го IP-то од `minikube ip` наместо 127.0.0.1)

### Демонстрација дека работи

```bash
kubectl get all -n dians-crypto
kubectl get configmaps,secrets,pvc,ingress -n dians-crypto
```

Апликацијата потоа е достапна на http://dians.local, а податоци се
повлекуваат со:

```bash
curl -X POST http://dians.local/run-fetch
```

Перзистентноста на базата може да се демонстрира со бришење на db pod-от -
StatefulSet-от го рекреира, а податоците остануваат (PVC):

```bash
kubectl delete pod dians-db-0 -n dians-crypto
kubectl get pods -n dians-crypto -w
```

## CD со Argo CD (GitOps)

Pipeline-от има и **deploy** job кој се извршува по успешниот build:
го запишува новиот таг на имиџите (краток git SHA) во `k8s/kustomization.yaml`
и прави commit. Argo CD го следи `k8s/` директориумот во репото и
автоматски ја поставува новата верзија на кластерот.

```bash
# Инсталација на Argo CD
kubectl create namespace argocd
kubectl apply -n argocd --server-side --force-conflicts -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# Application што го следи ова репо
kubectl apply -f argocd/application.yaml

# Веб интерфејс: https://localhost:8081 (корисник admin)
kubectl port-forward svc/argocd-server -n argocd 8081:443
```

> По секој push, CI прави commit со новите тагови, па пред следниот
> push треба `git pull --rebase`.
