# Vercel Deployment Guide

Deploying the Gmail AI Auto Labeler to Vercel requires configuring your environment variables correctly. 

Because Vercel is a serverless environment, the application will automatically:
1. Process webhooks synchronously (so they don't freeze).
2. Disable the APScheduler (to prevent memory leaks).
3. Use Vercel Cron Jobs to trigger the Watch Renewal and Retry queues.

> [!WARNING]
> **Database Requirement**
> You CANNOT use SQLite on Vercel because the disk is ephemeral. You must use a remote Postgres database (e.g. Supabase, Neon, Render, Railway).

## Deployment Steps

1. Commit all your code and push to a GitHub repository.
2. Go to the [Vercel Dashboard](https://vercel.com/new) and import your GitHub repository.
3. In the **Environment Variables** section, you MUST add every variable listed below.
4. Click **Deploy**.

## Environment Variables Checklist

Add all of these to your Vercel project before deploying:

### Core & Database
- `DATABASE_URL` (Must be a remote Postgres connection string starting with `postgresql://`)
- `SCHEDULER_ENABLED` = `false` (CRITICAL for Vercel)
- `VERCEL_CRON_SECRET` = (Generate a random secure string for cron authentication)

### Google & Gmail OAuth
- `GMAIL_CLIENT_ID` = (From Google Cloud Console)
- `GMAIL_CLIENT_SECRET` = (From Google Cloud Console)
- `GMAIL_PROJECT_ID` = (From Google Cloud Console)
- `GMAIL_AUTH_URI` = `https://accounts.google.com/o/oauth2/auth`
- `GMAIL_TOKEN_URI` = `https://oauth2.googleapis.com/token`
- `GMAIL_CERT_URL` = `https://www.googleapis.com/oauth2/v1/certs`
- `GMAIL_REDIRECT_URI` = `https://<YOUR_VERCEL_DOMAIN>/` (Must match the exact URL configured in Google Cloud Console)
- `GCP_PUBSUB_TOPIC` = (Your Google Cloud Pub/Sub topic for push notifications)
- `ENCRYPTION_KEY` = (A 32-byte url-safe base64 string for encrypting OAuth tokens in the DB)

### AI Configuration
- `GROQ_API_KEY` = (Your Groq API key)
- `GROQ_MODEL` = `llama-3.3-70b-versatile` (Or your preferred model)

## Post-Deployment

1. Update your Google Cloud Console OAuth Consent Screen with your new Vercel domain.
2. Add `https://<YOUR_VERCEL_DOMAIN>/` to your Authorized Redirect URIs in Google Cloud Console.
3. Add `https://<YOUR_VERCEL_DOMAIN>/webhook/gmail` to your Pub/Sub Push Subscription endpoint.
4. Visit `https://<YOUR_VERCEL_DOMAIN>/login` to authenticate your Gmail account.
5. Visit `https://<YOUR_VERCEL_DOMAIN>/watch/status` to activate instant Push Notifications!
