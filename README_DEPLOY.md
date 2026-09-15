# Deployment Notes

This project was prepared for deployment without changing the core business logic, auth flow, routes, forms, models, or user-facing templates.

## What changed

- `myproject/settings.py`
  - Keeps local `.env` loading.
  - Loads `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, and `DJANGO_CSRF_TRUSTED_ORIGINS` from environment variables.
  - Supports `DATABASE_URL` for deployment platforms such as Render.
  - Preserves the existing SQL Server-based local setup when `DATABASE_URL` is not set.
  - Adds `STATIC_ROOT` and production static storage support.
  - Adds proxy-aware HTTPS settings for deployment behind a reverse proxy.
- `requirements.txt`
  - Adds the packages needed for deployment with Gunicorn, WhiteNoise, and database URL parsing.
- `Procfile`
  - Defines the production web process.
- `runtime.txt`
  - Pins a deployment-friendly Python version.
- `.env.example`
  - Replaced with safe placeholders only.
- `../.gitignore`
  - Excludes local secrets, virtualenvs, caches, SQLite files, collected static files, and editor artifacts.

## Deployment root

The Django deployment root is the `myproject/` folder because it contains `manage.py`.

If you deploy from the parent folder, set the platform root directory to:

```text
myproject
```

## Local run after cleanup

From `myproject/`:

```powershell
copy .env.example .env
```

Then fill in the values you actually use locally, especially:

- `DJANGO_SECRET_KEY`
- `DJANGO_DB_*` if you use SQL Server locally
- Email variables if you want real emails locally

Install dependencies:

```powershell
pip install -r requirements.txt
```

Run the project:

```powershell
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py runserver
```

## Environment variables to set

Minimum required for deployment:

```text
DJANGO_SECRET_KEY=your-real-secret-key
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=your-app.onrender.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://your-app.onrender.com
SITE_BASE_URL=https://your-app.onrender.com
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.your-provider.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=your-email-user
EMAIL_HOST_PASSWORD=your-email-password
DEFAULT_FROM_EMAIL=Portail PGH <noreply@your-domain.com>
```

Database options:

### Recommended on Render

Use a managed PostgreSQL database and set:

```text
DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DBNAME
```

When `DATABASE_URL` is set, it takes priority over `DJANGO_DB_*`.

### Keep SQL Server

Leave `DATABASE_URL` empty and set:

```text
DJANGO_DB_ENGINE=mssql
DJANGO_DB_NAME=your-db-name
DJANGO_DB_USER=your-db-user
DJANGO_DB_PASSWORD=your-db-password
DJANGO_DB_HOST=your-db-host
DJANGO_DB_PORT=1433
DJANGO_DB_DRIVER=ODBC Driver 18 for SQL Server
DJANGO_DB_EXTRA_PARAMS=Encrypt=yes;TrustServerCertificate=no;
```

Note: if you deploy with SQL Server on Render or another Linux host, the server must also have the Microsoft ODBC driver installed. That is an infrastructure requirement outside the Django codebase.

## Render deployment steps

1. Push your code repository.
2. Create a new Web Service on Render.
3. Set the Root Directory to `myproject`.
4. Set the Build Command to:

```text
pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate
```

5. Set the Start Command to:

```text
gunicorn myproject.wsgi --log-file -
```

6. Add the environment variables listed above.
7. Deploy.

## Verification commands

Run these from `myproject/`:

```powershell
python manage.py check
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py runserver
```

## Folder structure after cleanup

The important deployment-facing structure is:

```text
App/
|-- .gitignore
|-- myproject/
|   |-- manage.py
|   |-- Procfile
|   |-- README_DEPLOY.md
|   |-- requirements.txt
|   |-- runtime.txt
|   |-- .env.example
|   |-- core/
|   |-- templates/
|   `-- myproject/
`-- venv/                  # local only, ignored
```

## Safe notes

- The local `.env` file was updated only to move previous in-code local defaults into environment variables, so the current machine setup stays stable after cleanup.
- Existing local virtualenv folders and local database files were not deleted to avoid destructive changes.
- They are now covered by ignore rules so they stay out of deployment and version control.
