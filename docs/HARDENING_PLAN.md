# Hardening plan — remaining code review findings

Findings from the review of 2026-09-11. Items **1, 4, 6 and 8** were implemented
on the `robustness-hardening` branch; this document covers the rest, in the
order I would do them.

Each item records the evidence, so nobody has to re-derive why it matters.

---

## Done on this branch

| # | Finding | What changed |
|---|---|---|
| 1 | No retry or rate-limit handling | `retry_wait` / `should_retry` in `targets/kentik.py`; writes only retried on 429/503 |
| 4 | State file written once, non-atomically | `save_state` uses temp + `os.replace`; both apply loops wrapped in `try/finally` |
| 6 | Bad numbers and unhandled errors return HTML 500 | `as_int` in `web/server.py`; `@app.errorhandler(Exception)` returns JSON; `plan.py` plan-id coercion guarded |
| 8 | Duplicated plan prologue | `derive_sites()` extracted in `plan.py` |

---

## 2. Circuit breaker on the apply loops

**Priority: high.** Cheap, and it directly limits the blast radius of the
failure mode most likely to happen on a customer tenant.

**Problem.** `apply_device_plan` and `apply_plan`
(`src/ibx_kentik_prepop/apply.py`) walk every entry regardless of what happened
to the previous one. A revoked token or an expired licence turns a 200-device
apply into 200 identical failures, and with the retry logic now in place each of
those failures is up to four requests. The operator watches a progress stream
fill with the same error 200 times.

**Fix.** Count consecutive failures. After a threshold (suggest 5, configurable
as `max_consecutive_failures`), stop the loop, emit a `done` event carrying the
reason and the number of entries not attempted, and record it in the run summary
so the state file says the run was abandoned rather than completed.

Do not abort on *total* failures — a tenant with a handful of genuinely bad
device names should still process the good ones. It is the *consecutive* run
that indicates something systemic.

**Tests.** Extend `tests/test_apply.py`: a Kentik stub that fails every call
should stop after the threshold, not after every entry; a stub that fails
intermittently should run to completion.

---

## 3. Pagination on the Kentik list endpoints

**Priority: high, but blocked on a tenant.** This is the only finding that could
silently corrupt a plan rather than merely annoy an operator.

**Problem.** `KENTIK.get_sites()` and `KENTIK.get_devices()`
(`src/ibx_kentik_prepop/targets/kentik.py`) read the first response body and
stop. Every other list in this codebase paginates — NIOS `_paging` /
`next_page_id`, UDDI `_page_token`, UAI cursor — so Kentik is the exception.

**Why it matters.** `device_index()` is what decides `create` vs `exists`. If
the device list is truncated server-side, an existing device that falls off the
first page is planned as a `create`. Best case Kentik rejects it on a duplicate
name; worst case it succeeds and burns a licensed slot. The same truncation on
`site_index()` would make devices land with no site.

**Blocked on.** The response shape. Nobody has confirmed whether
`GET /site/v202211/sites` and `GET /device/v202504beta2/device` return a
`pagination` block, a `total`, or an implicit cap.

**Do first (unblocked, useful either way):** log the returned count against any
`total` / `pagination` field present in the response, and raise a plan warning
when the two disagree. That turns a silent truncation into a visible one without
having to guess the pagination contract.

**Then:** once the shape is known, follow the cursor the same way the other
adapters do, with a page cap for safety.

---

## 5. Unguarded `response.json()` and unbounded page loops in the sources

**Priority: medium.** The Kentik client got this treatment as part of item 1;
the Infoblox sources did not.

**Problem.** `sources/nios.py:156`, `sources/uddi.py:127` and
`sources/uddi_uai.py:196` all call `response.json()` outside the `try`. A proxy
or captive portal answering 200 with HTML raises `ValueError` straight past the
careful `last_error` handling, out of `get_subnets()`, and — now that the web
layer has a JSON error handler — into a 500 that says `JSONDecodeError` rather
than "your proxy intercepted this".

Separately, `NIOS.get_all` loops on `next_page_id` with no page cap. A grid that
returns the same page id repeatedly hangs the tool. `UDDI.paginate` at least
breaks on an empty batch.

**Fix.** Move `response.json()` inside the existing `except`, catching
`ValueError` into `last_error` with the first 200 characters of the body (the
pattern `KENTIK._request` now uses — copy it). Add a `MAX_PAGES` guard to both
paginators that logs and stops rather than looping.

**Tests.** A scripted session returning HTML with a 200 should leave
`last_error` populated and return `[]`, not raise. A session that always returns
the same page id should stop at the cap.

---

## 7. Device names: empty results and collisions

**Priority: medium.** Low likelihood, but the failure is confusing when it
happens.

**Problem.** `sanitise_device_name` (`src/ibx_kentik_prepop/summarise.py`)
strips everything Kentik disallows:

```
'!!!'       -> ''            # POSTed as device_name: ''
'core-sw-1' -> 'core_sw_1'   # collides with...
'core sw 1' -> 'core_sw_1'   # ...this
```

An empty name reaches the API and comes back as an unhelpful 400. A collision is
worse: two distinct devices share a key in `device_index()`, so one can be
reported as `exists` when the device Kentik holds is actually the other one.

**Fix.**
- Fall back to an address-derived name (`10_1_2_3`) when the sanitised name is
  empty, and record that substitution in the report so it is visible.
- Add an empty/duplicate-name check to `device_apply_problems()` so both are
  caught at plan time with the offending names listed, rather than at write time
  one device at a time.
- Surface collisions in the device table as a warning column — the operator is
  the only one who can say whether `core-sw-1` and `core sw 1` are the same box.

**Tests.** Extend `tests/test_device_plan.py` with both cases.

---

## 9. The `model` ↔ `summarise` import cycle

**Priority: medium.** Pure maintenance, no behaviour change, but it is the thing
most likely to confuse the next person.

**Problem.** Six deferred function-level imports exist to dodge a cycle:

| Location | Imports |
|---|---|
| `model.py:289` | `summarise.sanitise_device_name` |
| `report.py:151-152` | `summarise.sanitise_device_name`, `kentik.device_description` |
| `export.py:243` | `summarise.sanitise_device_name` |
| `sources/base.py:266` | `summarise.tag_value` |
| `cli.py:265` | `kentik.camel`, `kentik.read_field` |

Only `model` ↔ `summarise` is a genuine cycle — `summarise` imports `model` at
module level. I verified the other four import cleanly at module level; they are
copied from the one case that needed it.

**Fix.** Move the pure string helpers `sanitise_device_name` and `tag_value`
into a leaf module (`names.py`, importing nothing from the package) or onto
`model.py` itself, which depends on nothing. Then hoist all six imports to the
top of their files where a reader can see the dependency graph.

**Tests.** The existing suite covers it — this is a pure move. Confirm with
`python3 -c 'import ibx_kentik_prepop.model'` and the full suite.

---

## 10. Dead code and one field that lies

**Priority: low, but do it in one sweep.**

| Item | Location | Action |
|---|---|---|
| `DEVICE_LICENCE_NOTE` | `targets/kentik.py:72` | Defined, never referenced. Delete, or use it — the same wording is already duplicated in `report.py`. |
| `reduced_fields` | `sources/base.py:97` | Set by both adapters, read by nobody. Either surface it in the report ("some fields were unavailable") or delete it. Surfacing is more useful — it is the symptom of the `os_version` problem. |
| `'device_writes_enabled': False` | `web/server.py:290` | Device writes **are** enabled. No client reads it. Delete it. |
| `ROLE_TO_SUBTYPE` | `targets/kentik.py:62` | Maps all four roles to `'router'`, so `DeviceConfig.subtype` is unreachable for any recognised role — a config knob that silently does nothing. Either drop the knob or let it win. |

The `ROLE_TO_SUBTYPE` one needs a decision rather than a mechanical edit: is
`device_subtype` ever meant to be anything but `router` for a pre-populated
device? If not, delete the config field; if so, make the map fall through to it.

---

## 11. Phase-1 wording that is now wrong

**Priority: low.** Mechanical, but this is the drift that makes a reader
distrust the comments — and the comments are the most valuable thing in this
repo.

| Location | Says | Truth |
|---|---|---|
| `targets/kentik.py:7` | "devices (v5 admin API)" | Devices use `v202504beta2`; only plans use v5 |
| `targets/kentik.py` class docstring | "Read and write sites, and read devices" | It writes devices |
| `apply.py:229` (`apply_plan`) | "Device writes are refused" | They are not; see `apply_device_plan` |
| `web/static/index.html:334` | "Devices are reported only… needs a plan_id" | True *for the Sites task*, but the stated reasons are obsolete. Should point at the Devices task. |

*(The stale `--replace-networks` reference in the README was fixed as part of
this branch — it contradicted the merge behaviour Kentik confirmed.)*

---

## 12. `devices.index(m)` compares by value

**Priority: low.** One line.

`plan.py:248` uses `devices.index(m)` to find an inferred gateway being
superseded. `Device` is a plain dataclass, so `index` deep-compares every field
including the `raw` source payload — expensive on a large discovery set, and
ambiguous if two devices ever compare equal.

**Fix.** `next(i for i, d in enumerate(devices) if d is m)` — identity is what
is meant.

---

## 13. Single-exit style is applied inconsistently

**Priority: low, needs a decision rather than a change.**

The house style is one `return` at the end of a function. `resolve_plan`,
`extract_address`, `read_field`, `cli.main` and every Flask handler return
early. The Flask handlers are idiomatic and I would leave them.

**Decide:** either exempt request handlers and short guard-style helpers
explicitly (and write that down), or bring the non-handler cases into line. The
rule is worth less than nothing if it is followed half the time — a reader
cannot tell an intentional exception from an oversight.

---

## Not a code change: the web UI has no authentication

`web_server.py` binds `127.0.0.1` by default, which is right. `--host` accepts
anything, and there is no authentication or authorisation of any kind — anyone
who can reach the port can apply to Kentik as the identity in the ini file, and
(without `--lock-config`) point it at any other readable ini on the host.

This is documented, and the single-operator model is a deliberate decision
carried over from `uddi_toolkit`. The cheap mitigation is a **startup warning
when the bind address is not loopback**, in the same place the existing
`--lock-config` advice is logged (`web/server.py`, `main()`). A per-request auth
model is a larger piece of work and should be a deliberate decision, not
something smuggled in with a hardening pass.
