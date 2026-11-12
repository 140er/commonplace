### Host container memory

- The host compose example runs the gateway with 20 GiB memory and 6 GiB swap (`mem_limit: 20g`, `memswap_limit: 26g`) as temporary headroom against memory-worker OOM kills (#275); `tests/test_commonplace_deploy.py` pins both values.
