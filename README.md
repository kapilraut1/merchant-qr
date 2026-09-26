# Merchant QR Settlement & Reconciliation

A simulation of how a digital wallet settles merchant QR payments and reconciles
the result. A customer scans a merchant's QR, the money lands in a wallet, a
nightly job batches the day's completed payments into a settlement less
commission, and a report compares what was paid against what was recorded.

```
customer wallet ──▶ /transactions ──▶ transactions ──┐
    (idempotent)      (signed invoice)                │
                                                       ▼
                            settlement_batches ◀── celery beat (00:05 UTC)
                                   │                  └── celery worker
                            settlement_items
                                   │
                                   ▼
                    /merchants/{id}/reconciliation
```

## Quick start

Requires Docker with the Compose plugin. Nothing else.

```bash
git clone <this repo>
cd python1
docker compose up --build
```

Then open **http://localhost:5173** and register a merchant.

| Service    | URL                                            |
| ---------- | ---------------------------------------------- |
| Dashboard  | http://localhost:5173                          |
| API docs   | http://localhost:8000/docs                     |
| Readiness  | http://localhost:8000/health/ready             |
| PostgreSQL | `localhost:5432` (`wallet` / `wallet` / `wallet`) |
| Redis      | `localhost:6379`                               |

The backend container runs `alembic upgrade head` before serving, so a clean
checkout comes up with the right schema and no manual step.

> On Windows, prefer `127.0.0.1` over `localhost` — `localhost` often resolves
> to IPv6 `::1`, which the containers do not listen on.

### Try the whole flow

1. **Dashboard → Simulate a customer paying.** Issue an invoice, create a
   wallet, pay. Press **Pay** again with the same idempotency key: the response
   is the original transaction with `idempotent_replay: true`, and the wallet is
   not charged twice.
2. **Dashboard.** Pick today's date, press **Run settlement**. A batch appears
   with gross, commission and net payout, and the reconciliation panel goes
   green.
3. Pay again *after* settling. The payment shows as an **orphaned payment** in
   the reconciliation panel, because money arrived that no batch covers. Run
   settlement again and it clears.

## Architecture

| Layer    | Choice                                                     |
| -------- | ---------------------------------------------------------- |
| API      | FastAPI, synchronous SQLAlchemy 2.0 sessions                 |
| Database | PostgreSQL 16 with Alembic migrations                        |
| Worker   | Celery on Redis, beat schedules the nightly run              |
| Frontend | React 18, Vite, Tailwind, built to static files behind nginx |
| Tests    | pytest against a real PostgreSQL instance                    |

Five domain entities: `Merchant`, `Wallet`, `Transaction`, `SettlementBatch`,
`SettlementItem`. Invoices are not a table — they are short-lived HMAC-signed
tokens, which is closer to how a real QR code behaves.

### Layout

```
backend/app/
  api/          HTTP routes only
  core/         security, invoice signing, money arithmetic
  models/       SQLAlchemy ORM
  schemas/      Pydantic request/response models
  services/     business logic (payments, settlements, reconciliation)
  tasks/        Celery tasks
backend/alembic/versions/   0001..0004
frontend/src/
  pages/        Login, Dashboard, PayQr
  components/   presentational pieces
k8s/manifests.yaml
.github/workflows/ci.yml
```

## Design decisions worth knowing

**Money is never a float.** Every amount is `NUMERIC(14,2)` in the database and
`Decimal` in Python. Rounding is `ROUND_HALF_UP`, not Python's default
`ROUND_HALF_EVEN`, because a merchant's statement says 0.005 becomes 0.01. A
test asserts `net_payout + commission == total` across a spread of inputs, since
the reconciliation report depends on it.

**Idempotency is a database guarantee.** `transactions.idempotency_key` is
UNIQUE, so a retried scan cannot charge twice even if two requests race. The
loser of the race reads back the winner's row. The `Idempotency-Key` header wins
over the body field; replaying a key with a *different* merchant is a 409, not
a silent echo of someone else's transaction.

**A payment and its debit share one commit.** The transaction insert and the
wallet balance update commit together, so the ledger can never record money that
did not move.

**Settlement is idempotent by construction.** A batch claims only completed
transactions that have no `SettlementItem`, and `settlement_items.transaction_id`
is UNIQUE. The merchant row is locked for the duration, so two concurrent runs
cannot build overlapping batches. The scheduled task and the manual endpoint call
the same service function.

**Settlement freezes the commission.** A batch stores the commission and net
payout it calculated. Reconciliation therefore checks the batch's *internal*
arithmetic (`net == total - commission`) instead of re-deriving
`total * commission_rate` — otherwise raising a merchant's rate would mark every
historical batch as broken.

**Readiness and liveness are different on purpose.** `/health` touches no
dependency so a database blip cannot trigger a restart loop; `/health/ready`
verifies PostgreSQL is reachable.

**Money-moving routes are unauthenticated on purpose.** `/transactions` and
`/wallets` stand in for the customer's wallet app, where the signed invoice and
the caller's own wallet are the authorisation. Merchant-facing routes
(`/merchants/{id}/...`) all require a JWT and additionally verify the token's
merchant matches the path, returning 403 otherwise.

## API

| Method | Path                                        | Auth | Purpose                          |
| ------ | ------------------------------------------- | ---- | -------------------------------- |
| POST   | `/merchants/register`                       | –    | Create a merchant, returns a JWT  |
| POST   | `/merchants/login`                          | –    | Exchange credentials for a JWT    |
| GET    | `/merchants/me`                             | JWT  | Current merchant                 |
| POST   | `/merchants/{id}/invoices`                  | JWT  | Mint a signed QR payload         |
| GET    | `/merchants/{id}/transactions?date=`        | JWT  | A day's rows plus the day's total |
| POST   | `/merchants/{id}/settle`                    | JWT  | Run settlement now (demo trigger) |
| GET    | `/merchants/{id}/settlements`               | JWT  | Settlement history, newest first |
| GET    | `/merchants/{id}/settlements/{batch_id}`    | JWT  | One batch with its items         |
| GET    | `/merchants/{id}/reconciliation`            | JWT  | Mismatch report                  |
| POST   | `/transactions`                             | –    | Pay an invoice (idempotent)      |
| GET    | `/transactions/{id}`                        | –    | One transaction                  |
| POST   | `/wallets`                                  | –    | Open a wallet                    |
| GET    | `/wallets/{id}/balance`                     | –    | Current balance                  |
| POST   | `/wallets/{id}/top-up`                      | –    | Add funds                        |

### Payment semantics

`POST /transactions` takes an `Idempotency-Key` header (or `idempotency_key`
field). It answers **201** for a new payment and **200** for a replay, with
`idempotent_replay: true` and the original transaction. Insufficient funds
records a `failed` transaction and leaves the balance alone.

### Reconciliation

The report names three ways the books can disagree:

- **`orphaned_payments`** — completed transactions no batch covered.
- **`amount_mismatches`** — `sum(settlement_items.amount) != batch.total_amount`.
- **`commission_mismatches`** — `net_payout != total_amount - commission_deducted`.

`summary.is_balanced` is true only when all three are empty. Detail lists are
capped by `?limit`, but the summary counts and the orphaned total are always
exact, and `detail_truncated` says when a page was clipped.

## Configuration

Backend settings are environment variables (see `backend/app/config.py`):

| Variable              | Default                    | Notes                                    |
| --------------------- | -------------------------- | ---------------------------------------- |
| `DATABASE_URL`        | `postgresql+psycopg://…`   |                                          |
| `REDIS_URL`           | `redis://redis:6379/0`     | Celery broker and result backend         |
| `JWT_SECRET`          | `dev-secret-change-me`     | **Change this outside development.**     |
| `JWT_EXPIRE_MINUTES`  | `720`                      |                                          |
| `CORS_ORIGINS`        | `*`                        | Comma-separated in production.           |
| `SETTLEMENT_TIMEZONE` | `UTC`                      | Decides which local day a payment lands in. |

Frontend: `VITE_API_BASE_URL` is baked into the bundle at build time.

## Tests

```bash
# from the repo root
docker compose exec backend pytest -q
docker compose exec backend ruff check .
```

```bash
# or locally, against a PostgreSQL you can reach
cd backend
pip install -e '.[dev]'
TEST_DATABASE_URL=postgresql+psycopg://wallet:wallet@localhost:5432/wallet_test pytest -q
```

The suite creates a dedicated `<database>_test` database, builds the schema
with `Base.metadata.create_all`, and runs each test inside a transaction that is
rolled back. Postgres is required — not SQLite — because the behaviour under
test includes `NUMERIC` precision, `SELECT … FOR UPDATE`, partial unique
violations, and real concurrent connections.

`tests/test_concurrency.py` runs eight real threads against real connections. It
found two bugs during development: a session left unusable after losing the
insert race, and a lost wallet update caused by committing the insert and the
debit separately.

## CI

`.github/workflows/ci.yml` runs three jobs on every push and pull request:

- **backend** — ruff, format check, and pytest against a `postgres:16` service.
- **frontend** — `npm ci`, ESLint, and a production Vite build.
- **compose** — validates the Compose file and builds both images.

## Kubernetes

`k8s/manifests.yaml` is a basic manifest set: namespace, ConfigMap, Secret,
PostgreSQL (StatefulSet with a volume claim), Redis, and Deployments for the
API, frontend, Celery worker and beat.

```bash
kubectl apply -f k8s/manifests.yaml
```

It ships placeholder credentials in the Secret. Replace them out of band, and
point the images at your registry:

```bash
kubectl -n merchant-settlement create secret generic backend-secrets \
  --from-literal=POSTGRES_PASSWORD=... \
  --from-literal=DATABASE_URL=... \
  --from-literal=REDIS_URL=... \
  --from-literal=JWT_SECRET=... --dry-run=client -o yaml | kubectl apply -f -
```

Beat is a singleton (`replicas: 1`, `Recreate`) because two schedulers would
publish the nightly batch twice. The API and worker use the same image, and
`alembic upgrade head` runs as an init container.

## Notes and limitations

- Wallets and payments are unauthenticated, matching the wallet-app simulation.
  Real deployment needs a token on the customer side too.
- Settlement creates batches; it does not move money. A real payout step (bank
  transfer) and its own idempotency key would sit between the batch and the
  merchant's account.
- `settlement_status` has `pending`/`failed` values for an asynchronous payout
  flow. Nothing sets them yet.
- Celery tasks run with the process timezone set to UTC.
- Docker bind mounts on Windows break `--reload` (`Cannot allocate memory`), so
  the backend runs without it. Use `docker compose restart backend` after editing
  backend code.
