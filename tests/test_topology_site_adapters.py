"""Site route discovery, the read-only parts: the DNS step with a fake resolver and the
two reverse proxy adapters on invented configs (example names, documentation addresses)."""

import builtins
import json
import os
import socket
import subprocess
import time

import pytest

from commonplace.topology import SecretRefused, TopologyError, proxy_adapters, site_dns
from commonplace.topology.proxy_adapters import ProxyAdapter, Upstream
from commonplace.topology.site_dns import DnsAnswer, DnsUnavailable

# Secret-shaped values are assembled here so no file holds one in a single piece.
HASH = "$2a$14$" + "Zkx19XLiW6VYouLHR5NmfOFU0z2GTNmpkT5qqR7hx4IjWJPDhjvGe"
TOKEN = "sk-" + "live0123456789abcdef0123456789abcdef"
DNS_TOKEN = "dnsapi" + "9f8e7d6c5b4a39281706f5e4d3c2b1a0"
PEM = (
    "-----BEGIN " + "PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----"
)
BASIC = "YWxpY2U6" + "aHVudGVyMg=="
PASSWORD = "hunter2" + "-proxy"
SECRETS = (HASH, TOKEN, DNS_TOKEN, "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC", BASIC, PASSWORD)

CADDYFILE = f"""{{
	email admin@example.com
	acme_dns exampledns {DNS_TOKEN}
}}

(protected) {{
	basic_auth {{
		alice {HASH}
	}}
}}

# The blog and its API.
example.com, www.example.com {{
	import protected
	tls /etc/ssl/example.pem /etc/ssl/example.key
	reverse_proxy blog:3000 {{
		header_up Authorization "Bearer {TOKEN}"
	}}
	handle /api/* {{
		reverse_proxy 192.0.2.20:8080
	}}
}}

https://shop.example.com:443 {{
	reverse_proxy /admin* localhost:9000
	reverse_proxy :8081
}}

static.example.com {{
	root * /srv/static
	file_server
}}
"""

CADDY_JSON = json.dumps(
    {
        "admin": {"identity": {"issuers": [{"module": "acme", "api_token": TOKEN}]}},
        "apps": {
            "tls": {"certificates": {"load_pem": [{"certificate": "cert", "key": PEM}]}},
            "http": {
                "servers": {
                    "srv0": {
                        "listen": [":443"],
                        "routes": [
                            {
                                "match": [{"host": ["example.net", "www.example.net"]}],
                                "handle": [
                                    {
                                        "handler": "subroute",
                                        "routes": [
                                            {
                                                "handle": [
                                                    {
                                                        "handler": "authentication",
                                                        "providers": {
                                                            "http_basic": {
                                                                "accounts": [
                                                                    {
                                                                        "username": "alice",
                                                                        "password": HASH,
                                                                    }
                                                                ]
                                                            }
                                                        },
                                                    },
                                                    {
                                                        "handler": "reverse_proxy",
                                                        "transport": {
                                                            "protocol": "http",
                                                            "tls": {},
                                                        },
                                                        "upstreams": [
                                                            {"dial": "198.51.100.20:8443"}
                                                        ],
                                                    },
                                                ]
                                            },
                                            {
                                                "match": [{"path": ["/files/*"]}],
                                                "handle": [
                                                    {
                                                        "handler": "reverse_proxy",
                                                        "upstreams": [{"dial": "files:9000"}],
                                                    }
                                                ],
                                            },
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                }
            },
        },
    }
)

NPM_ROWS = [
    {
        "id": 3,
        "domain_names": ["example.org", "www.example.org"],
        "forward_scheme": "http",
        "forward_host": "wiki",
        "forward_port": 8080,
        "enabled": 1,
        "access_list": {"items": [{"username": "alice", "password": PASSWORD}]},
        "certificate": {"meta": {"dns_provider_credentials": f"dns_api_token = {DNS_TOKEN}"}},
        "advanced_config": f'proxy_set_header Authorization "Basic {BASIC}";',
        "locations": [
            {
                "path": "/api",
                "forward_scheme": "https",
                "forward_host": "192.0.2.30",
                "forward_port": 9443,
            }
        ],
    },
    {
        "id": 4,
        "domain_names": ["off.example.org"],
        "forward_host": "old",
        "forward_port": 80,
        "enabled": 0,
    },
    {
        "id": 5,
        "domain_names": ["gone.example.org"],
        "forward_host": "old",
        "forward_port": 80,
        "is_deleted": 1,
    },
]

NPM_NGINX = f"""# ------------------------------------------------------------
# example.org
# ------------------------------------------------------------

server {{
  set $forward_scheme http;
  set $server         "wiki";
  set $port           8080;

  listen 80;
  listen 443 ssl http2;

  server_name example.org www.example.org;

  ssl_certificate /etc/letsencrypt/live/npm-3/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/npm-3/privkey.pem;
  auth_basic_user_file /data/access/2;
  proxy_set_header Authorization "Basic {BASIC}";
  include /data/nginx/custom/server_proxy.conf;

  location /api {{
    proxy_set_header X-Api-Key {TOKEN};
    proxy_pass https://192.0.2.30:9443;
  }}

  location / {{
    include conf.d/include/proxy.conf;
  }}
}}

server {{
  set $forward_scheme https;
  set $server         "2001:db8::30";
  set $port           8443;
  server_name files.example.org;
  location / {{
    include conf.d/include/proxy.conf;
  }}
}}
"""


def fake_resolver(records):
    def resolve(name):
        resolve.calls.append(name)
        return records.get(name, DnsAnswer())

    resolve.calls = []
    return resolve


def sites(parsed):
    return {entry.names: entry for entry in parsed.sites}


def targets(entry):
    return [(u.scheme, u.host, u.port, u.path) for u in entry.upstreams]


# ── The DNS step ──────────────────────────────────────────────────────────
def test_dns_step_reports_the_alias_chain_and_addresses_with_evidence():
    resolver = fake_resolver(
        {
            "example.com": DnsAnswer(
                addresses=("203.0.113.10", "2001:DB8::10", "203.0.113.10"),
                cnames=("Edge.Example.NET.",),
            )
        }
    )
    assert site_dns.lookup("Example.COM.", resolver) == {
        "name": "example.com",
        "cnames": ["edge.example.net"],
        "addresses": ["2001:db8::10", "203.0.113.10"],
        "evidence": [
            "example.com CNAME edge.example.net",
            "edge.example.net AAAA 2001:db8::10",
            "edge.example.net A 203.0.113.10",
        ],
    }
    assert resolver.calls == ["example.com"]


def test_dns_step_says_when_a_name_has_no_records():
    answer = site_dns.lookup("missing.example.com", fake_resolver({}))
    assert answer["addresses"] == [] and answer["cnames"] == []
    assert answer["evidence"] == ["missing.example.com has no address records"]


@pytest.mark.parametrize(
    "name",
    [
        "localhost",
        "web1",
        "192.0.2.1",
        "2001:db8::1",
        "nas.local",
        "app.internal",
        "db.home.arpa",
        "site.test",
        "printer.lan",
        "under_score.example.com",
        "-bad.example.com",
        "example..com",
        "a" * 60 + ".example.com",
        "",
        None,
        7,
    ],
)
def test_only_public_names_are_looked_up(name):
    resolver = fake_resolver({})
    with pytest.raises(TopologyError, match="domain name"):
        site_dns.lookup(name, resolver)
    assert resolver.calls == []


def test_dns_step_keeps_only_hostnames_and_addresses_from_a_resolver():
    resolver = fake_resolver(
        {
            "example.com": DnsAnswer(
                addresses=("203.0.113.10", "not an address", 7, "999.1.1.1"),
                cnames=("bad name!", "example.com", None, "edge.example.net"),
            )
        }
    )
    answer = site_dns.lookup("example.com", resolver)
    assert answer["addresses"] == ["203.0.113.10"] and answer["cnames"] == ["edge.example.net"]


def test_dns_step_fails_closed_when_the_resolver_fails_or_hangs():
    def broken(name):
        raise OSError("resolver down at 192.0.2.53")

    with pytest.raises(DnsUnavailable, match="lookup failed") as failure:
        site_dns.lookup("example.com", broken)
    assert "192.0.2.53" not in str(failure.value)
    with pytest.raises(DnsUnavailable, match="lookup failed"):
        site_dns.lookup("example.com", lambda name: ["203.0.113.10"])
    with pytest.raises(DnsUnavailable, match="timed out"):
        site_dns.lookup("example.com", lambda name: time.sleep(5), seconds=0.05)


def test_system_resolver_reads_addresses_and_the_canonical_name(monkeypatch):
    asked = []

    def getaddrinfo(name, port, **options):
        asked.append((name, port, options["flags"]))
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "edge.example.net", ("203.0.113.10", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::10", 0, 0, 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    assert site_dns.lookup("example.com")["evidence"] == [
        "example.com CNAME edge.example.net",
        "edge.example.net AAAA 2001:db8::10",
        "edge.example.net A 203.0.113.10",
    ]
    assert asked == [("example.com", None, socket.AI_CANONNAME)]

    def missing(name, port, **options):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", missing)
    assert site_dns.system_resolver("example.com") == DnsAnswer()

    def down(name, port, **options):
        raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")

    monkeypatch.setattr(socket, "getaddrinfo", down)
    with pytest.raises(DnsUnavailable):
        site_dns.system_resolver("example.com")


# ── Caddy ─────────────────────────────────────────────────────────────────
def test_caddyfile_sites_and_upstreams_with_their_lines():
    parsed = proxy_adapters.parse("caddy", CADDYFILE)
    found = sites(parsed)
    assert list(found) == [
        ("example.com", "www.example.com"),
        ("shop.example.com",),
        ("static.example.com",),
    ]
    blog = found[("example.com", "www.example.com")]
    assert blog.evidence == "line 13: example.com, www.example.com {"
    assert blog.upstreams == (
        Upstream("blog", 3000, None, "", "line 16: reverse_proxy blog:3000"),
        Upstream(
            "192.0.2.20", 8080, None, "/api/*", "line 20: reverse_proxy /api/* 192.0.2.20:8080"
        ),
    )
    shop = found[("shop.example.com",)]
    assert targets(shop) == [(None, "localhost", 9000, "/admin*"), (None, "localhost", 8081, "")]
    assert proxy_adapters.primary(shop).port == 8081
    assert found[("static.example.com",)].upstreams == ()
    assert parsed.withheld == 0


def test_caddyfile_forms_one_site_snippets_upstream_blocks_and_placeholders():
    one_site = "example.com\n\nencode gzip\nreverse_proxy h2c://app.example.net:8443\n"
    (entry,) = proxy_adapters.parse("caddy", one_site).sites
    assert entry.names == ("example.com",)
    assert targets(entry) == [("h2c", "app.example.net", 8443, "")]

    text = """
(app) {
	reverse_proxy {
		to app-1:8080 app-2:8080
		lb_policy round_robin
	}
}
http://example.com, :8080 {
	import app
	import /etc/caddy/sites/*.caddy
}
"dash.example.com" {
	route /grafana* {
		reverse_proxy \\
			[2001:db8::7]:3000
	}
	reverse_proxy @internal unix//run/dash.sock
	reverse_proxy {$BACKEND}
}
"""
    found = sites(proxy_adapters.parse("caddy", text))
    assert list(found) == [("example.com", "*"), ("dash.example.com",)]
    assert targets(found[("example.com", "*")]) == [
        (None, "app-1", 8080, ""),
        (None, "app-2", 8080, ""),
    ]
    assert found[("example.com", "*")].upstreams[0].evidence == (
        "line 4: reverse_proxy { to app-1:8080 }"
    )
    assert targets(found[("dash.example.com",)]) == [
        (None, "2001:db8::7", 3000, "/grafana*"),
        (None, "localhost", None, "@internal"),
        (None, "", None, ""),
    ]


def test_caddy_json_config_routes_and_subroutes():
    parsed = proxy_adapters.parse("caddy", CADDY_JSON)
    (entry,) = parsed.sites
    assert entry.names == ("example.net", "www.example.net")
    assert entry.evidence == "route match host: example.net, www.example.net"
    assert entry.upstreams == (
        Upstream(
            "198.51.100.20", 8443, "https", "", "reverse_proxy upstream dial: 198.51.100.20:8443"
        ),
        Upstream("files", 9000, None, "/files/*", "reverse_proxy upstream dial: files:9000"),
    )
    assert proxy_adapters.parse("caddy", '{"apps": {"http": {"servers": []}}}').sites == ()


# ── Nginx Proxy Manager ───────────────────────────────────────────────────
def test_npm_proxy_host_rows():
    parsed = proxy_adapters.parse("nginx_proxy_manager", json.dumps(NPM_ROWS))
    (entry,) = parsed.sites
    assert entry.names == ("example.org", "www.example.org")
    assert entry.evidence == "proxy host 3: domain_names example.org, www.example.org"
    assert entry.upstreams == (
        Upstream("wiki", 8080, "http", "", "proxy host 3: forward http://wiki:8080"),
        Upstream(
            "192.0.2.30",
            9443,
            "https",
            "/api",
            "proxy host 3: location /api forward https://192.0.2.30:9443",
        ),
    )


def test_npm_rows_as_the_table_stores_them_and_under_a_list_key():
    row = {
        "id": 9,
        "domain_names": '["db.example.org"]',
        "forward_host": "2001:db8::9",
        "forward_port": "5984",
        "locations": "[]",
    }
    for text in (json.dumps(row), json.dumps({"proxy_hosts": [row, "junk", None]})):
        (entry,) = proxy_adapters.parse("nginx_proxy_manager", text).sites
        assert entry.names == ("db.example.org",)
        assert targets(entry) == [("http", "2001:db8::9", 5984, "")]
    assert proxy_adapters.parse("nginx_proxy_manager", "[]").sites == ()
    assert proxy_adapters.parse("nginx_proxy_manager", '[{"domain_names": 7}]').sites == ()


def test_npm_generated_nginx_files():
    parsed = proxy_adapters.parse("nginx_proxy_manager", NPM_NGINX)
    found = sites(parsed)
    assert list(found) == [("example.org", "www.example.org"), ("files.example.org",)]
    wiki = found[("example.org", "www.example.org")]
    assert wiki.evidence == "line 5: server_name example.org www.example.org"
    assert wiki.upstreams == (
        Upstream(
            "wiki",
            8080,
            "http",
            "",
            "line 7: set $forward_scheme http; set $server wiki; set $port 8080",
        ),
        Upstream(
            "192.0.2.30",
            9443,
            "https",
            "/api",
            "line 23: location /api proxy_pass https://192.0.2.30:9443",
        ),
    )
    assert targets(found[("files.example.org",)]) == [("https", "2001:db8::30", 8443, "")]


def test_plain_nginx_server_blocks_and_unknown_variables():
    text = """
http {
  server {
    server_name _ ~^www\\d+\\.example\\.org$ app.example.org;
    location / { proxy_pass http://127.0.0.1:5000/; }
    location /other { proxy_pass http://$backend; }
    location /scheme { proxy_pass $forward_scheme://$server:$port; }
  }
}
"""
    (entry,) = proxy_adapters.parse("nginx_proxy_manager", text).sites
    assert entry.names == ("*", "app.example.org")
    assert targets(entry) == [
        ("http", "127.0.0.1", 5000, ""),
        ("http", "", None, "/other"),
        (None, "", None, "/scheme"),
    ]


# ── Secrets never leave an adapter ────────────────────────────────────────
@pytest.mark.parametrize(
    "kind, text",
    [
        ("caddy", CADDYFILE),
        ("caddy", CADDY_JSON),
        ("nginx_proxy_manager", json.dumps(NPM_ROWS)),
        ("nginx_proxy_manager", NPM_NGINX),
    ],
)
def test_adapters_drop_auth_hashes_tokens_and_keys(kind, text):
    assert any(secret in text for secret in SECRETS)
    parsed = proxy_adapters.parse(kind, text)
    shown = repr(parsed)
    assert parsed.sites and not [secret for secret in SECRETS if secret in shown]
    assert "alice" not in shown and "Authorization" not in shown
    assert parsed.withheld == 0


def test_a_site_or_upstream_line_that_looks_secret_is_withheld_and_counted():
    text = f"""
example.com {{
	reverse_proxy https://alice:{PASSWORD}@app.example.net:8443
	reverse_proxy /hook* https://hooks.example.net/{TOKEN}
	reverse_proxy /key* {TOKEN}:443
}}
"""
    parsed = proxy_adapters.parse("caddy", text)
    (entry,) = parsed.sites
    assert entry.upstreams == (
        Upstream(
            "app.example.net",
            8443,
            "https",
            "",
            "line 3: reverse_proxy https://app.example.net:8443",
        ),
        Upstream(
            "hooks.example.net",
            None,
            "https",
            "/hook*",
            "upstream line withheld: it looked like a secret",
        ),
        Upstream("", 443, None, "/key*", "upstream line withheld: it looked like a secret"),
    )
    assert parsed.withheld == 2
    assert TOKEN not in repr(parsed) and PASSWORD not in repr(parsed)

    rows = [
        {"domain_names": ["example.org"], "forward_host": f"u:{PASSWORD}@wiki", "forward_port": 1}
    ]
    parsed = proxy_adapters.parse("nginx_proxy_manager", json.dumps(rows))
    assert PASSWORD not in repr(parsed)
    assert targets(parsed.sites[0]) == [("http", "wiki", 1, "")]


def test_adapters_read_nothing_but_the_text_and_run_nothing(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("an adapter touched the machine")

    texts = [
        ("caddy", "import /etc/caddy/secrets.caddy\n" + CADDYFILE),
        ("caddy", CADDY_JSON),
        ("nginx_proxy_manager", NPM_NGINX),
        ("nginx_proxy_manager", json.dumps(NPM_ROWS)),
    ]
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", refuse)
        patch.setattr(os, "open", refuse)
        patch.setattr(os, "system", refuse)
        patch.setattr(os, "getenv", refuse)
        patch.setattr(subprocess, "Popen", refuse)
        patch.setattr(socket, "socket", refuse)
        patch.setattr(socket, "getaddrinfo", refuse)
        results = [proxy_adapters.parse(kind, text) for kind, text in texts]
    assert all(parsed.sites for parsed in results)


# ── The adapter interface ─────────────────────────────────────────────────
def test_serving_prefers_the_name_then_a_wildcard_then_any_name():
    text = """
:80 {
	reverse_proxy fallback:1
}
*.example.com {
	reverse_proxy wild:2
}
blog.example.com {
	reverse_proxy blog:3
}
"""
    parsed = proxy_adapters.parse("caddy", text)

    def host(site):
        return proxy_adapters.serving(parsed, site).upstreams[0].host

    assert host("blog.example.com") == "blog"
    assert host("shop.example.com") == "wild"
    assert host("example.org") == "fallback"
    only = proxy_adapters.parse("caddy", "blog.example.com {\n\treverse_proxy blog:3\n}\n")
    assert proxy_adapters.serving(only, "example.org") is None


def test_another_proxy_is_one_registered_adapter(monkeypatch):
    class LineAdapter(ProxyAdapter):
        kind = "lines"
        label = "Lines"

        def read(self, text, found):
            for number, line in enumerate(text.splitlines(), 1):
                name, _, target = line.partition(" -> ")
                found.upstream(found.site([name], f"line {number}: {name}"), target, line)

    monkeypatch.setattr(proxy_adapters, "ADAPTERS", dict(proxy_adapters.ADAPTERS))
    proxy_adapters.register(LineAdapter())
    (entry,) = proxy_adapters.parse("lines", "example.com -> app:3000").sites
    assert entry.names == ("example.com",) and targets(entry) == [(None, "app", 3000, "")]


def test_refusals_never_repeat_the_config():
    with pytest.raises(TopologyError, match="proxy kind must be one of caddy, nginx_proxy_manager"):
        proxy_adapters.parse("traefik", CADDYFILE)
    for text in ("", "   ", None, 7, [CADDYFILE]):
        with pytest.raises(TopologyError, match="config must be text"):
            proxy_adapters.parse("caddy", text)
    with pytest.raises(TopologyError, match="larger than") as failure:
        proxy_adapters.parse("caddy", CADDYFILE + "#" * proxy_adapters.MAX_CONFIG)
    assert DNS_TOKEN not in str(failure.value)
    assert not issubclass(TopologyError, SecretRefused)


def test_broken_configs_do_not_raise():
    for kind in ("caddy", "nginx_proxy_manager"):
        for text in ("}}}{{{", '{"apps": 7}', "[1, 2]", "server {", "a {\n b {\n", '"unterminated'):
            assert isinstance(proxy_adapters.parse(kind, text).sites, tuple)
    deep = "example.com {\n" + "route {\n" * 40 + "reverse_proxy app:1\n" + "}\n" * 41
    assert proxy_adapters.parse("caddy", deep).sites[0].upstreams == ()
