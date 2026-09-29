# Accounts (optional)

The OpenMousse app can ask people to sign in before they connect a claw. That's what lets the project see who uses the
app and with which claws. Self-hosted builds can leave this out entirely, and then the app opens straight to the connect page.

**What an account holds:** email, an optional name, and the claws a person connects (the assistant's name, the server
address, the claw's kind, when it was last seen). **Never** tokens, chats, memory or connector credentials: those
stay on the person's own claw. If the account service is down, claws that are already connected keep working.

## How it works

- Sign-in is a 6-digit code sent by email (no passwords; no Apple or Google sign-in, so the App Store doesn't require
  Sign in with Apple). On iOS the code from Mail shows above the keyboard.
- The service is [Supabase](https://supabase.com) (Auth + one table). The app talks to it directly with the project's
  public key; row-level security keeps each person to their own rows.
- The app keeps the refresh token in the keychain and the access token in memory (`app/src/api/account.ts`).
- Settings shows the account card; Account has sign out and delete account (deletes the account and its claw list,
  nothing on any claw).

## Setting up your own

1. Create a Supabase project and run [`account/supabase.sql`](account/supabase.sql) in its SQL editor
   (table `claws` with row-level security, `delete_user()`, and an `owner_overview` view for you).
2. Authentication → Sign In / Providers → Email: keep email sign-in on. Emails → SMTP: use your own sender
   (the built-in one only sends a few emails an hour to your own team). Emails → templates **Magic link** and
   **Confirm signup**: put the code in them, e.g. `Your code: {{ .Token }}`. Set the OTP length to 6.
3. In the app's identity file (`app/app.local.<name>.json`) add
   `"account": {"url": "https://<project>.supabase.co", "anonKey": "<publishable or anon key>"}` and build or publish
   an update with `MOUSSE_LOCAL=app.local.<name>.json`.

Without the `account` key the build has no account screens at all.
