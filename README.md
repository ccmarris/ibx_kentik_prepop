# ibx_kentik_prepop

Pre-populate Kentik **sites** (and report **device** candidates) from Infoblox
NIOS or Universal DDI data.

A new Kentik deployment starts with an empty Sites list. Sites are what give
flow data business meaning — site-based traffic classification, per-site
dashboards, the site/interface architecture views — and the data needed to build
them already exists in Infoblox: subnets, site metadata in Extensible
Attributes or tags, and, where licensed, discovered device inventory from
Network Insight or Universal Asset Insights.

This tool reads that, derives a clean set of sites with **summarised** subnet
lists, shows you exactly what it would do, and then pushes it.

- Sites are created and updated. **Sites are never deleted.**
- Dry run is the default. Nothing is written without `--go`.
- Devices are **reported only** in this version (see [Devices](#devices)).

## Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

Python 3.10+.

## Credentials

Copy `ibx_kentik.ini.example` to `ibx_kentik.ini` (or keep it in `~/configs`)
and fill in the sections you need. Only the source platform you use is
required, plus `[KENTIK]` when applying.

```ini
[NIOS]
gm = 192.168.1.10
user = admin
pass = infoblox
wapi_version = v2.13.1
valid_cert = false

[UDDI]
api_key = <service api key>
base_url = https://csp.infoblox.com

[KENTIK]
email = user@example.com
token = <api token>
base_url = https://api.kentik.com
grpc_base_url = https://grpc.api.kentik.com
```

Resolution priority is **CLI flag → environment variable → ini file**. The
environment variables are `IBX_NIOS_GM`, `IBX_NIOS_USER`, `IBX_NIOS_PASS`,
`IB_API_KEY`, `IB_BASE_URL`, `KENTIK_EMAIL`, `KENTIK_TOKEN`,
`KENTIK_BASE_URL` and `KENTIK_GRPC_BASE_URL`.

**EU tenants:** set `base_url = https://csp.eu.infoblox.com` under `[UDDI]`
and `https://api.kentik.eu` / `https://grpc.api.kentik.eu` under `[KENTIK]`.

## Usage

Find out which EA/tag keys are actually populated:

```bash
./ibx_kentik_prepop.py -c ibx_kentik.ini --source uddi --list-keys
```

Dry run — the default, and the thing you should read carefully:

```bash
./ibx_kentik_prepop.py -c ibx_kentik.ini --source uddi --site-key Site
```

Apply it:

```bash
./ibx_kentik_prepop.py -c ibx_kentik.ini --source uddi --site-key Site --go
```

Other useful runs:

```bash
# NIOS, restricted to one network view, with the device section
./ibx_kentik_prepop.py --source nios --site-key Location \
    --network-view internal --devices --use-insight

# One site at a time while you validate the mapping
./ibx_kentik_prepop.py --source uddi --site-key Site --site-filter 'LON-*' --go

# Machine-readable output
./ibx_kentik_prepop.py --source uddi --site-key Site -o json --outfile plan.json
./ibx_kentik_prepop.py --source uddi --site-key Site -o csv  --outfile plan.csv
```

`-o csv --outfile plan.csv` writes `plan-sites.csv`, `plan-subnets.csv`,
`plan-devices.csv` and `plan-warnings.csv`.

Behaviour that isn't a credential can go in a YAML file
(`-y config.yaml`); see `config.example.yaml`. API paths and the site type map
live there too, so a moved endpoint is a config change rather than a code
change.

## How sites are derived

1. **Group** subnets by the value of the site EA/tag (`--site-key`). One
   distinct value becomes one Kentik site. Values are whitespace-normalised and
   compared case-insensitively, so `LON-DC1 ` and `lon-dc1` are one site (and
   the merge is reported).
2. **Skip** subnets with no value for that key — and say so in the report.
   Unattributed subnets are a data quality finding, not something to bucket
   silently.
3. **Summarise** each site's subnets with an exact collapse
   (`ipaddress.collapse_addresses`): adjacent and contained prefixes merge, and
   the result covers exactly the same address space as the input. Setting
   `--max-prefix-len N` additionally aggregates up to `/N` — that *is* lossy,
   so a supernet is only accepted when it does not overlap another site's
   space. Where it would, the exact prefixes are kept and the refusal is
   reported.
4. **Classify** each subnet into Kentik's three address classification buckets:
   - an explicit classification EA/tag (`--class-key`, values
     `infrastructure` / `user_access` / `other`) always wins;
   - otherwise `/30`–`/32`, loopback and link-local space, and subnets whose
     name or comment matches `mgmt|management|transit|p2p|loopback|infra|wan`
     become **infrastructure**;
   - everything else becomes **user access**. `other` is only ever explicit.

   The classification of every prefix is in the report, so it can be corrected
   before you push — either here or, better, in IPAM.

## What is written to Kentik

Sites use the **Site API v202211** (`POST/PUT /site/v202211/sites`). The
summarised prefix lists map onto the site's `addressClassification`:

| Internal bucket | Kentik field |
|---|---|
| `infrastructure` | `infrastructureNetworks` |
| `user_access` | `userAccessNetworks` |
| `other` | `otherNetworks` |

Matching is by site **title**, case-insensitively. An unknown title is created;
a known one is updated. By default the derived prefixes are **added** to
whatever Kentik already holds, so prefixes added by hand in the portal survive;
`--replace-networks` makes the derived lists authoritative. `siteMarket`,
`architecture`, `postalAddress` and the coordinates of an existing site are
preserved on update.

Applied runs are recorded in `ibx_kentik_prepop_state.json` (site name, Kentik
id, action, timestamp, and the last 50 run summaries).

## Devices

Device candidates come from Network Insight (`--use-insight`, NIOS only),
Universal Asset Insights (`--use-uai`, UDDI only), or — when neither is
available — inference from the DHCP `routers` option (`--use-gateways`), which
is flagged as inferred rather than discovered in the report.

They are **reported only**. The report includes the exact `POST /api/v5/device`
payload for each candidate. Three reasons the write is not wired up:

1. Every Kentik device consumes a **licensed device slot**.
2. `plan_id` has to be chosen by the operator.
3. `sending_ips` must be the **flow exporter source address**, which is not
   necessarily the discovered management IP. Get it wrong and you create a
   device that never matches any flow.

Review the payloads, then create the devices in Kentik.

## Web interface

```bash
./web_server.py -c ibx_kentik.ini -v
```

Then open <http://127.0.0.1:5000>. The UI drives the same code: pick the
credentials file, source and site key (both have pickers showing what is
available), run a dry run, read the warnings, and apply — the apply run is
streamed live as Server-Sent Events.

### Switching credentials from the UI

The **Credentials ini file** field overrides the file the server was started
with, per request — useful when you look after several tenants and don't want a
restart between them. The picker lists the `*.ini` files in the startup file's
directory, the project directory and `~/configs`, labelled with the sections
each one actually contains (`[NIOS]`, `[UDDI]`, `[KENTIK]`). Relative paths
resolve against the project directory and `~` is expanded. A file is only
accepted if it exists and contains at least one of those three sections; values
are never sent to the browser, only the file path and the section names.

Start the server with `--lock-config` to pin it to the startup ini and refuse
overrides — the field is disabled in the UI and the API rejects the request.

Single-operator model: credentials come from the server-side ini, so anyone who
can reach the UI acts as that identity — and, unless you use `--lock-config`,
can point it at any ini file that the server's user can read. It binds to
`127.0.0.1` by default; keep it that way unless you put authentication in front
of it. The apply endpoint requires an explicit confirmation in the request
body.

## Verify before running against a customer tenant

Three things are worth confirming per tenant, all flagged inline in the code:

1. **Kentik auth header names** (`X-CH-Auth-Email` / `X-CH-Auth-API-Token`) —
   everything depends on them. A read-only
   `GET /site/v202211/sites` is the cheapest possible check.
2. **Network Insight field names.** `discovery:device` has changed between NIOS
   releases; check the grid's own `/wapidoc`. The raw payload is retained on
   each device so the flattening can be corrected without re-pulling.
3. **The UAI asset category** covering routers, switches and firewalls.
   `compute` is confirmed for virtual machines; the network infrastructure
   category needs confirming against the tenant (`uddi.asset_category` in the
   YAML).

## Tests

```bash
python3 -m pytest -q
```

No network access required — the suite covers summarisation, classification,
name sanitisation against Kentik's 4–60 alphanumeric/underscore device name
rule, the create/update/no-change diff, apply behaviour, and the report
renderers.

## Not in scope

- **Kentik custom dimension populators.** Pushing IPAM tags into Kentik as
  populators is a different job with its own ordering trap (Kentik keeps the
  most recently added populator, which is *not* longest-prefix-match). See
  `../ideas/files/push_populators.py`.
- **UDDI `Locations` objects as a site source.** They carry a real postal
  address and coordinates and would be a good addition; this version derives
  sites from subnet metadata and discovery data only.
