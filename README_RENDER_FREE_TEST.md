# Render Free Test Deployment

This folder is for a no-payment Render demo/test deployment only. It avoids paid Render disks and uses Render free-tier plans where available.

## Important Free-Tier Limitations

Do not use this setup for real student/faculty data.

- The web service uses Render's free plan and can spin down after inactivity. First load can be slow.
- The filesystem is ephemeral. Uploaded Excel files, exports, and backup artifacts can disappear on restart/redeploy.
- The PostgreSQL database is configured with Render's free database plan and may expire according to Render's current free-tier policy.
- Backups are not reliable in this setup because they are written to `/tmp`.
- Password reset emails use Django's console backend by default, so emails appear in logs instead of being delivered.

## Deploy Steps

1. Push the contents of this `ToRenderFreeTest/` folder to a new GitHub repository.
2. In Render, choose `New +` then `Blueprint`.
3. Select the GitHub repository that contains this folder's files.
4. Render should detect `render.yaml`.
5. Confirm creation. It should create:
   - a free web service
   - a free PostgreSQL database, if available in your Render account/region
6. After the first deploy, open the Render service shell and create an admin user:

```bash
python manage.py createsuperuser
```

7. Open the Render URL and log in.

## If Render Still Asks For Payment

Render pricing and free-tier availability can vary by account, region, and policy changes. If Blueprint still asks for payment, create services manually:

1. Create a free PostgreSQL database if the option is available.
2. Create a free web service from your GitHub repo.
3. Use build command: `./build.sh`
4. Use start command: `./start.sh`
5. Add environment variables manually:
   - `DJANGO_ENV=prod`
   - `DJANGO_DEBUG=false`
   - `DJANGO_USE_SQLITE=false`
   - `DJANGO_RENDER_FREE_TEST=true`
   - `DJANGO_SECRET_KEY=<generate a long random value>`
   - `DATABASE_URL=<your Render PostgreSQL internal/external connection string>`
   - `DJANGO_MEDIA_ROOT=/tmp/med-student-tracker/media`
   - `DJANGO_PROTECTED_EXPORT_ROOT=/tmp/med-student-tracker/protected_exports`
   - `BACKUP_FULL_ROOT=/tmp/med-student-tracker/backups/full`
   - `BACKUP_DAILY_SQL_ROOT=/tmp/med-student-tracker/backups/daily_sql`
   - `DJANGO_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend`

## Production Reminder

For real use, use the paid-style `ToRender/` folder or another production host with persistent database, durable file storage, email delivery, and backups.
