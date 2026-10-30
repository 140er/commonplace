# Social sign-in

Buttons appear after all of a provider's variables are in `/srv/commonplace/.env` on edge1 and the gateway restarts.
With none set, sign-in stays unchanged. Keep credentials in that file only.

Use **Commonplace** as the app name and `https://commonplace.run` as the homepage/website URL where requested. The gateway requests the scopes below.

## Google

Google Cloud Console (`console.cloud.google.com`) → select/create project → Google Auth Platform → Branding / Audience / Data Access: configure the consent screen, External audience, scopes `openid email profile`, and authorized domains `commonplace.run`, `cmmn.dev`. Clients → Create client → Web application → Authorized redirect URIs: `https://app.cmmn.dev/app/auth/google/callback`.
Client ID → `COMMONPLACE_GOOGLE_CLIENT_ID`; client secret → `COMMONPLACE_GOOGLE_CLIENT_SECRET`.
No sensitive-scope verification for these scopes; displaying the app name/logo publicly requires brand verification, including domain ownership and published homepage/privacy policy.

## GitHub

GitHub → Settings → Developer settings → OAuth Apps → New OAuth App → Register application. Authorization callback URL: `https://app.cmmn.dev/app/auth/github/callback`; scopes `read:user user:email`.
Client ID → `COMMONPLACE_GITHUB_CLIENT_ID`; Generate a new client secret → `COMMONPLACE_GITHUB_CLIENT_SECRET`.

## Apple

Apple Developer (`developer.apple.com/account`) → Certificates, Identifiers & Profiles → Identifiers → + → App IDs: register an explicit App ID with Sign in with Apple enabled as primary. Identifiers → + → Services IDs: register, then Sign in with Apple → Configure → select that primary App ID. Domain: `app.cmmn.dev`; return URL: `https://app.cmmn.dev/app/auth/apple/callback` (POST); scopes `name email`.
Keys → + → Sign in with Apple → Configure → same primary App ID → Register → Download the `.p8` key.
Services ID → `COMMONPLACE_APPLE_CLIENT_ID`; Membership details → Team ID → `COMMONPLACE_APPLE_TEAM_ID`; key ID → `COMMONPLACE_APPLE_KEY_ID`; full `.p8` text → `COMMONPLACE_APPLE_PRIVATE_KEY` (literal `\n` is accepted).
Apple's web setup guide explicitly says domain registration needs no server file upload; `apple-developer-domain-association.txt` is not required.

## X

X Developer Console (`console.x.com`; legacy portal: `developer.x.com` → Projects & Apps) → Create App/select app → Settings → User authentication settings → Set up/Edit. Enable OAuth 2.0; choose **Web App** (confidential) and **Request email from users**; supply the published Terms of service and Privacy policy URLs. Callback/Redirect URL: `https://app.cmmn.dev/app/auth/x/callback`; scopes `tweet.read users.read users.email`.
Keys and tokens → OAuth 2.0 Client ID → `COMMONPLACE_X_CLIENT_ID`; client secret → `COMMONPLACE_X_CLIENT_SECRET` (use OAuth 2.0 credentials, not the API key/secret).

## Discord

Discord Developer Portal (`discord.com/developers/applications`) → New Application → Create → OAuth2 → Redirects → Add Redirect: `https://app.cmmn.dev/app/auth/discord/callback` → Save Changes; scopes `identify email`.
OAuth2 Client ID → `COMMONPLACE_DISCORD_CLIENT_ID`; client secret → `COMMONPLACE_DISCORD_CLIENT_SECRET`.

## Accounts and cost

Set `COMMONPLACE_SIGNUP=open` to allow a new verified email to create a user and personal workspace; otherwise new-account signup is closed.
Known provider accounts return to the same user; a verified email matching an existing account links to it.
Unverified email never links or creates an account; an already linked provider account uses its saved identity.

Google, GitHub and Discord sign-in are free. Apple requires the paid Developer Program: US$99/year; €99/year needs confirmation at local checkout before Jeffrey approves enrollment.
X uses prepaid API credits: one `GET /2/users/me` per sign-in is approximately US$0.01 at the published user-read rate; repeat reads of the same user within a UTC day are generally deduplicated. Confirm the rate in the console before funding.
