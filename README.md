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

Two tasks, selected with `--task` (or the switch at the top of the web UI):

- **Sites data** — derive sites, their summarised subnets, and their postal
  address and coordinates where the metadata exists.
- **Device data** — derive routers, switches and firewalls from Network Insight
  or UAI, place them on those sites, and create them in Kentik for flow or NMS.

Ground rules throughout:

- Dry run is the default. Nothing is written without `--go`.
- Sites and devices are only ever created or updated. **Nothing is deleted.**
- Kentik **merges** site subnet lists on a PUT and offers no field mask or
  PATCH, so nothing this tool submits can remove a prefix from a site. Prefixes
  Kentik holds that your Infoblox data does not account for are reported as
  extras, to remove in the portal if you want them gone.
- Creating a device consumes a licensed device slot, so the device apply checks
  the plan's remaining capacity and stops rather than overrunning it.

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

Section names are matched case-insensitively, so `[Kentik]`, `[kentik]` and
`[KENTIK]` all work. A few key spellings are accepted per credential:
`email`/`api_email`/`user`, `token`/`api_token`/`api_key`, `base_url`/`url`,
`gm`/`grid_master`/`host`, `user`/`username`, `pass`/`password`, and
`wapi_version`/`api_version`/`version`.

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
./ibx_kentik_prepop.py -c ibx_kentik.ini --source uddi
```

`--site-key` defaults to **`Site`**, the conventional EA/tag name, so it only
needs supplying when your metadata uses something else (`--site-key Location`).
If the key matches nothing, the report says so and points at `--list-keys`
rather than quietly producing no sites.

Apply it:

```bash
./ibx_kentik_prepop.py -c ibx_kentik.ini --source uddi --site-key Site --go
```

Then the devices, once those sites exist:

```bash
# dry run: what would be created, on which sites, with which sending IPs
./ibx_kentik_prepop.py -c ibx_kentik.ini --task devices --site-key Site --use-uai

# create them for flow on the free plan, skipping one
./ibx_kentik_prepop.py -c ibx_kentik.ini --task devices --site-key Site --use-uai \
    --exclude-device nyc-fw-01 --go

# create them as NMS devices instead
./ibx_kentik_prepop.py -c ibx_kentik.ini --task devices --site-key Site --use-insight \
    --device-mode nms --agent-id <agent> --credential-name snmp-ro --go
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

Export the same plan as Kentik-shaped import artefacts:

```bash
./ibx_kentik_prepop.py --source uddi --site-key Site --devices --use-insight \
    --export-kentik exports/tenant-a
```

See [Kentik import artefacts](#kentik-import-artefacts) for what each file is.

Behaviour that isn't a credential can go in a YAML file
(`-y config.yaml`); see `config.example.yaml`. API paths and the site type map
live there too, so a moved endpoint is a config change rather than a code
change.

## How sites are derived

1. **Group** subnets by the value of the site EA/tag (`--site-key`, default
   `Site`). One
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

## Kentik import artefacts

`--export-kentik PREFIX` writes the plan in shapes Kentik itself consumes, so
you can hand the work to someone else, put it through a change process, or load
it with Kentik's own tooling instead of letting this tool write:

| File | What it is for |
|---|---|
| `PREFIX-sites.json` | Site API v202211 request bodies, each with the `method` and `path` to send it to. `POST` for new sites, `PUT /sites/{id}` for existing ones. |
| `PREFIX-sites.csv` | Flat site table (title, type, action, id, one column per classification bucket) for review or exchange. |
| `PREFIX-devices.json` | v5 admin API device request bodies, ready to `POST /api/v5/device`. |
| `PREFIX-devices-add.csv` | The columns Kentik's own [`kentik_add_device.py`](https://github.com/kentik/kentik_add_device) loader reads: `siteid,devicename,devicedescription,sendingips,v6add,asn,devicesnmpcommunity,devicesamplerate,planid`. |
| `PREFIX-devices-nms.csv` | The columns the portal's NMS bulk device import accepts: `name,address,agent_id`. `agent_id` is left empty — the portal then uses the first available agent. |

Only artefacts with something in them are written. The device CSVs follow the
mode — `devices-add.csv` in flow mode, `devices-nms.csv` in NMS mode — and
excluded devices are omitted from all of them. On a `--task devices` run only the
device artefacts are produced. Sites needing no change are excluded unless you
pass `--export-include-unchanged`.

Two things to know:

- **Kentik does not import site CSV.** The portal's Sites page exports CSV but
  will not ingest it, so `PREFIX-sites.json` is the artefact that can actually
  be applied. The CSV is for humans.
- **Create the sites before the devices.** A device carries `site_id`, and that
  is only filled in for sites that already exist in Kentik — so either apply the
  sites with `--go` first, or export again once they exist. The report warns
  which sites are still missing.

Replaying the site JSON is a one-liner:

```bash
python3 - <<'EOF'
import json, requests
session = requests.Session()
session.headers.update({'X-CH-Auth-Email': '...', 'X-CH-Auth-API-Token': '...'})
for item in json.load(open('exports/tenant-a-sites.json')):
    response = session.request(item['method'],
                               'https://grpc.api.kentik.com' + item['path'],
                               json=item['body'])
    print(item['method'], item['path'], response.status_code)
EOF
```

## Devices

Device candidates come from Network Insight (`--use-insight`, NIOS only) and
Universal Asset Insights (`--use-uai`, UDDI only), both **on by default** — each
exists on only one platform, so enabling both simply means "use whichever
discovery this platform has", and the inapplicable one is skipped silently.
Turn either off with `--no-use-insight` / `--no-use-uai`.

Inference from the DHCP `routers` option (`--use-gateways`) is **off by
default** and **additive**: it supplements discovery rather than replacing it,
its devices are flagged as inferred rather than discovered, and a device that
discovery also found always wins over the inferred one. Candidates are
de-duplicated on the Kentik device name, falling back to the management
address, and the report header lists which sources contributed.

If discovery returns nothing and gateway inference is off, the report says so
and names the switch to turn on, rather than showing an empty device list.

**Source API failures are never silent.** Both discovery adapters ask for more
fields than every release knows about — `description`, `location` and
`extattrs` on `discovery:device`, and `description`/`comment`/`tags` in the UAI
asset projection. A platform that does not recognise one of those names rejects
the *whole* request, which is indistinguishable from having no devices. So each
adapter retries with a core field set it knows is safe, keeps the devices, and
reports the degradation as a `device_source_error` warning naming the field it
lost. Any other API failure surfaces the same way.

**Interfaces and sending IPs.** Network Insight interfaces
(`discovery:deviceinterface`) and UAI asset addresses become the device's
interface list. `sending_ips` defaults to the management address
(`--sending-ips mgmt`), can be every discovered address (`--sending-ips all`),
and can be chosen per device from the interface list in the UI. This matters:
for a flow device the sending IP must be the **flow exporter source address**, or
the device sits idle.

**Descriptions.** The Kentik `device_description` leads with whatever the source
data says about the device — a `description` or `comment` field on the
discovered device or asset, or an EA/tag named `description`, `comment`,
`comments`, `notes`, `purpose` or `role` (configurable as
`device.description_keys`). The discovered vendor, model and version are
appended, so `Site edge router, WAN uplink - Cisco ISR4451 17.6`. With no
source text it is just the hardware summary, and gateway-inferred routers get
`Default gateway for 10.1.0.0/25 - <subnet comment>`. Descriptions are capped at
255 characters (Kentik does not document a limit — flagged in the code).

**Site matching.** A device is placed by, in order: its own location attribute
when that names a site we derived, then the most specific subnet containing any
of its **interface** addresses, then its management address. The report's
`matched by` column says which rule won — a device placed by a /8 is a much
weaker claim than one placed by the /30 on its uplink.

**Flow or NMS** (`--device-mode`, default `flow`). Both go to the same endpoint
(`POST /device/v202504beta2/device`); the mode decides which fields are
populated:

| Mode | Fields |
|---|---|
| `flow` | `device_subtype`, `plan_id`, `sending_ips`, `device_sample_rate` (default **1**, unsampled — `--sample-rate` to change), `minimize_snmp`, `device_snmp_ip` |
| `nms` | `nms{agent_id, ip_address, snmp{credential_name, port}}`, `monitoring_template_id` |

**BGP.** Kentik requires `device_bgp_type` on every device create, so it is
always sent — `none` by default, meaning "use generic IP/ASN mapping". The
accompanying boolean `device_bgp_flowspec` is sent as `false` by default. Both
are selectable (`--bgp-type`, `--bgp-flowspec`, or the controls in the UI):

| `--bgp-type` | Meaning | Also required |
|---|---|---|
| `none` (default) | generic IP/ASN mapping | nothing |
| `device` | peer with the device itself | `--bgp-neighbor-asn` and `--bgp-neighbor-ip` and/or `--bgp-neighbor-ip6` |
| `other_device` | share an already-peered device's routing table | `--bgp-device-id` |

The dependent fields are checked before anything is written, so a missing ASN
is a refusal up front rather than a 400 from the API halfway through a run.

**SNMP collection** is a separate choice from what the device is *for*
(`--snmp-mode`, or the dropdown in the UI), because a traffic device can have a
Universal Agent polling it:

| `--snmp-mode` | What it does | Payload |
|---|---|---|
| `none` (default) | no SNMP | — |
| `community` | Kentik polls with a community string (YAML only, to keep it out of the browser) | `device_snmp_community` |
| `agent-flow` | **Agent-based SNMP for Flow Enrichment (Traffic device)** — the agent polls interface data to enrich this device's flow | `nms{agent_id, ip_address, snmp{credential_name, port}}` |
| `agent-full` | **Agent-based SNMP for Full Monitoring** — the agent also collects the full NMS metric set | the same, plus `monitoring_template_id` |

`--device-mode nms` implies `agent-full`. Both agent modes **require an agent**
(`--agent-id`, or the dropdown, populated from `GET /kagent/v202401/agents` —
listed as `<id> - <name> (<status>)`, since the id is what gets submitted and
the name is what tells them apart; note the name lives on the agent's
`config.name`, not at the top level of the agent object) —
a device created without one is never polled, so the apply refuses. Credentials
come from `GET /credential/v202407alpha1/group`; a missing one warns rather than
blocks, and full monitoring without a template warns that Kentik will apply its
own default.

The agent is attached through the device's `nms` block, which is the only agent
field the device API has — the portal surfaces it as the *Collection Agent* on a
device's SNMP tab.

**`device_snmp_ip` and `device_snmp_community` are the legacy configuration** —
Kentik polling the device itself — and sending either of them alongside the
agent block makes the portal report the device as using the legacy method. They
are therefore only sent in `community` mode; in the agent modes the poll target
is `nms.ip_address` and nothing else.

**`flow_snmp_credential_name` is not the field for this, and is never sent.**
The name suggested it was the flow-side credential, but a tenant rejected it
outright:

```
400 At path: request.device.flow_snmp_credential_name --
    Expected a value of type `never`, but received: `"marrison-test"`
```

A `never` type means the write schema forbids it, so the agent's credential
goes in `nms.snmp.credential_name` and nowhere else. `device.flow_snmp_credential_name`
still exists as an escape hatch should Kentik ever accept it, but setting it
will almost certainly be rejected.

### Settling what the portal writes

When a setting does not come out as expected, read a device back rather than
guessing:

```bash
# configure one device by hand in the portal, then:
./ibx_kentik_prepop.py -c ibx_kentik.ini --show-device lon_rtr_01
```

That prints the SNMP and agent fields first — `device_agent_type`,
`snmp_enabled`, `device_snmp_ip`, `flow_snmp_credential_name`,
`monitoring_template_id`, `nms` — then the whole device as Kentik holds it.
Comparing that against what the tool sends is the fastest way to pin a mapping,
and several of the fields in that list are read-only, so they show what Kentik
decided rather than what was asked for.

**`snmp_enabled` is the field to watch.** It is read-only and derived: a device
carrying `device_snmp_ip` comes back as `snmpEnabled: "V2"`, which is what the
portal reports as the legacy SNMP method — even when the community string is
empty and nothing is actually being polled.

### A note on field naming

The device API accepts `snake_case` on writes but answers in
`lowerCamelCase` (`deviceSnmpIp`, `sendingIps`, `flowSnmpCredentialName`), and
the read model nests `site` and `plan` as objects. Every read in this tool goes
through a spelling-tolerant lookup for that reason, and device updates are built
from an explicit list of the fields the write message accepts — echoing a read
response back would otherwise return `plan`, `site`, `labels` and a multi-kilobyte
`customColumns` string to an endpoint that does not want them.

**The licence plan.** Plans are read from `GET /api/v5/plans` and resolved by
name — **`Free Flowpak Plan`** by default (Kentik's no-cost flow plan), matched
case-insensitively with underscores and spaces treated as equivalent. If that
exact name is absent, the closest containing match is used (so `Free Flowpak`
still resolves), then any active plan mentioning "free", then the first active
plan — and every substitution is reported rather than made quietly. Licensing is not visible to every service
account: when the API returns nothing, supply the id yourself with `--plan-id`
(or the **Plan id** field in the UI) and it is used as given. Before writing,
devices-to-create is checked against `max_devices` minus the devices already on
the plan; the run stops unless `--allow-over-capacity` is set.

**Devices that already exist** (matched on the Kentik device name) are reported
as `exists` with their id, along with any difference between the derived site and
sending IPs and what Kentik holds. They are **not** touched unless you pass
`--update-devices`, which updates only those two fields, read-modify-write: the
device is fetched, the two fields replaced, and the whole object PUT back, so
monitoring configuration this tool knows nothing about survives.

**Excluding devices.** `--exclude-device NAME` (repeatable, matching the
discovered name, the Kentik name or the management IP), `--exclude-file FILE`,
or the tick box per row in the UI. Excluded devices are skipped by both the
apply and the export, and shown struck through in the report.

## Web interface

```bash
./web_server.py -c ibx_kentik.ini -v
```

Then open <http://127.0.0.1:5000>. The UI drives the same code: pick the
credentials file, source and site key (both have pickers showing what is
available), run a dry run, read the warnings, and apply. **Build export**
produces the same artefacts as `--export-kentik` with a download button per
file.

**Check the Kentik API** in the Environment card issues a read-only
`GET /sites` and reports what came back, so you can prove the credentials and
auth headers work before attempting a write. When Apply is disabled the hint
under the buttons says exactly why.

### The task switch

**Sites data** and **Device data** at the top of the form select the task. The
site fields hide on a device run and vice versa, the results panels swap, and
the Apply button relabels — the two jobs never share a screen.

Changing a device tick box, a sending-IP selection, the mode or the plan
invalidates the reviewed plan — and then **re-runs the dry run for you** so the
fingerprint stays in step with what is on screen. Untick devices until you are
within the plan's capacity and Apply enables itself a moment later; there is no
need to press Dry run again. When Apply is disabled the reasons are listed under
the buttons, with the numbers (for example `6 device(s) to create exceeds the 4
slot(s) left on plan 'Free Flowpak Plan'`).

### Applying from the UI

The apply calls the Kentik API in-process and streams one result per site, so
the table fills in as sites are written and a failure shows the status code and
error body Kentik returned next to the site that failed. Two things gate it:

- **You confirm the actual diff.** The confirmation lists every changing site
  with the prefixes being added (`+`) and removed (`−`) per classification
  bucket — not just a count.
- **You can only apply what you reviewed.** The dry run returns a fingerprint
  of its outcome. The apply rebuilds the plan server-side and refuses with a
  409 if the fingerprint no longer matches, so an edited form or changed IPAM
  data can never be written under a diff you approved for something else. Run
  the dry run again and review the new diff.

Sites are never deleted, and device creation is still refused (see
[Devices](#devices)).

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

Confirmed with Kentik: a site **PUT merges** subnet information rather than
replacing it, and there is no field mask or PATCH on the Site API — which is why
this tool never claims to remove a prefix.

Three things are still worth confirming per tenant, all flagged inline in the
code:

1. **Kentik auth header names** (`X-CH-Auth-Email` / `X-CH-Auth-API-Token`) —
   everything depends on them. A read-only
   `GET /site/v202211/sites` is the cheapest possible check.
2. **Network Insight field names.** `discovery:device` has changed between NIOS
   releases; check the grid's own `/wapidoc`. The raw payload is retained on
   each device so the flattening can be corrected without re-pulling.
3. **The UAI asset category** covering routers, switches and firewalls.
   `compute` is confirmed for virtual machines; the network infrastructure
   category needs confirming against the tenant (`uddi.asset_category` in the
   YAML, default `network`).
4. **Whether a device PUT merges or replaces**, which decides how safe
   `--update-devices` is. The read-modify-write approach assumes replace, which
   is the conservative choice either way. NMS `plan_id` requirements are also
   unconfirmed — it is only sent when set.

## Tests

```bash
python3 -m pytest -q
```

No network access required — the suite covers summarisation, classification,
address/geo extraction, interface-to-site matching, plan resolution and
capacity, device payloads per mode, exclusions, apply behaviour and the report
renderers.

### Testing the whole thing offline

`tests/mock_kentik_api.py` is a standalone mock of both APIs — UDDI IPAM and
asset search on one side, and the Kentik site, plan, agent, credential and
device endpoints on the other. It reproduces the behaviours that matter:

- a site `PUT` **merges** the subnet lists, so the hand-added prefix it seeds
  survives an update;
- a device create is **rejected without `device_bgp_type`**, exactly as the real
  API does;
- the free plan has four device slots against six device candidates, so the
  capacity guard is exercised.

```bash
cp tests/mock.ini.example tests/mock.ini
python3 tests/mock_kentik_api.py &

./ibx_kentik_prepop.py -c tests/mock.ini --site-key Site --go
./ibx_kentik_prepop.py -c tests/mock.ini --task devices --site-key Site --use-uai \
    --exclude-device lon-sw-03 --exclude-device nyc-rtr-02 --go

curl -s localhost:8899/device/v202504beta2/device | python3 -m json.tool
```

`GET /_calls` on the mock returns every request it received, if you want to
assert on exactly what was sent. The web UI can be pointed at it the same way:
`./web_server.py -c tests/mock.ini`.

## Not in scope

- **Kentik custom dimension populators.** Pushing IPAM tags into Kentik as
  populators is a different job with its own ordering trap (Kentik keeps the
  most recently added populator, which is *not* longest-prefix-match). See
  `../ideas/files/push_populators.py`.
- **UDDI `Locations` objects as a site source.** They carry a real postal
  address and coordinates and would be a good addition; this version derives
  sites from subnet metadata and discovery data only.
