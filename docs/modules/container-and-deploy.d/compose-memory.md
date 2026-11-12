- **Owns:**

  - The host compose example gives the gateway 20 GiB of memory with 6 GiB swap (`mem_limit: 20g`, `memswap_limit: 26g`) so large memory workspaces are not OOM-killed (#275); this is temporary headroom until the root-cause fix ships.
