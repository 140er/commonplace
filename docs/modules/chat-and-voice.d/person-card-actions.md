- **Owns:**

  - Only chat listing and the exact project page accept the consented phone chat capability. Settings, model probes, status and search keep their previous person-session boundary. The web response and client stay unchanged.
  - `phone_list.summaries` adds phone-only `status` and `last_message` fields after readable boards have been selected. Activity uses `console.authority.agents` and `console.authority.status`, the same visibility and classifier used by web chat. No readable recorded activity means null.
- **Promises:**

  - One batched summary projection uses fixed queries across authorized projects: console-visible activity, the last shared board message, and this person's latest private front reply. It issues no per-project queries. The latest candidate is compared as a UTC instant. Other people's private replies and private bot threads never influence preview content, ordering or counts.
  - Previews use the web feed redactor before stripping HTML tags and truncating to 240 Unicode characters. Every phone entry contains nullable status and last_message; ordinary web entries retain their original keys.
