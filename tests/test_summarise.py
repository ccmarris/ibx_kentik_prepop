#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''
Tests for site derivation, summarisation and classification
'''

import ipaddress
from conftest import make_config, record
from ibx_kentik_prepop.model import (CLASS_INFRASTRUCTURE, CLASS_OTHER,
                                     CLASS_USER_ACCESS)
from ibx_kentik_prepop.summarise import (aggregate, build_sites, classify_subnet,
                                         collapse, derive_site_type,
                                         extract_address,
                                         find_cross_site_overlaps,
                                         normalise_site_name,
                                         sanitise_device_name, tag_value)


def nets(*cidrs):
    return [ipaddress.ip_network(c) for c in cidrs]


def test_normalise_site_name_collapses_whitespace():
    assert normalise_site_name('  LON   DC1 ') == 'LON DC1'
    assert normalise_site_name(None) == ''


def test_collapse_merges_adjacent_and_contained():
    result = collapse(nets('10.1.0.0/25', '10.1.0.128/25', '10.2.0.0/24',
                           '10.2.0.0/25'))
    assert [str(n) for n in result] == ['10.1.0.0/24', '10.2.0.0/24']


def test_collapse_keeps_address_families_apart():
    result = collapse(nets('10.1.0.0/24', '2001:db8::/64'))
    assert [str(n) for n in result] == ['10.1.0.0/24', '2001:db8::/64']


def test_aggregate_disabled_by_default():
    result, refused = aggregate(nets('10.1.0.0/24'), 0, [])
    assert [str(n) for n in result] == ['10.1.0.0/24']
    assert refused is False


def test_aggregate_lifts_to_the_prefix_floor():
    result, refused = aggregate(nets('10.1.0.0/24', '10.1.1.0/24'), 16, [])
    assert [str(n) for n in result] == ['10.1.0.0/16']
    assert refused is False


def test_aggregate_refused_when_it_would_swallow_another_site():
    result, refused = aggregate(nets('10.1.0.0/24'), 16, nets('10.1.9.0/24'))
    assert [str(n) for n in result] == ['10.1.0.0/24']
    assert refused is True


def test_classify_small_prefix_is_infrastructure():
    config = make_config()
    assert classify_subnet(record('10.1.0.0/30'), config) == CLASS_INFRASTRUCTURE


def test_classify_by_comment_pattern():
    config = make_config()
    assert classify_subnet(record('10.1.0.0/24', comment='Site mgmt vlan'),
                           config) == CLASS_INFRASTRUCTURE


def test_classify_default_is_user_access():
    config = make_config()
    assert classify_subnet(record('10.1.0.0/24', comment='Staff wifi'),
                           config) == CLASS_USER_ACCESS


def test_classify_override_wins():
    config = make_config()
    assert classify_subnet(record('10.1.0.0/30', class_override='other'),
                           config) == CLASS_OTHER
    assert classify_subnet(record('10.1.0.0/30', class_override='user access'),
                           config) == CLASS_USER_ACCESS


def test_classify_unknown_override_falls_back_to_heuristic():
    config = make_config()
    assert classify_subnet(record('10.1.0.0/24', class_override='nonsense'),
                           config) == CLASS_USER_ACCESS


def test_derive_site_type_maps_and_defaults():
    config = make_config()
    assert derive_site_type(record('10.1.0.0/24', site_type='DC'),
                            config) == 'SITE_TYPE_DATA_CENTER'
    assert derive_site_type(record('10.1.0.0/24'),
                            config) == 'SITE_TYPE_BRANCH'
    assert derive_site_type(record('10.1.0.0/24', site_type='site_type_cloud'),
                            config) == 'SITE_TYPE_CLOUD'


def test_sanitise_device_name():
    assert sanitise_device_name('lon-rtr-01') == 'lon_rtr_01'
    assert sanitise_device_name('r1') == 'r1_dev'
    assert sanitise_device_name('a' * 70) == 'a' * 60
    assert sanitise_device_name('10.1.0.1') == '10_1_0_1'
    assert sanitise_device_name('') == ''


def test_build_sites_groups_and_summarises():
    config = make_config()
    records = [record('10.1.0.0/25', site='LON-DC1'),
               record('10.1.0.128/25', site='LON-DC1'),
               record('10.2.0.0/24', site='NYC-BR2')]
    sites, warnings = build_sites(records, config)

    assert [s.name for s in sites] == ['LON-DC1', 'NYC-BR2']
    assert [s.cidr for s in sites[0].subnets] == ['10.1.0.0/24']
    assert sites[0].source_count() == 2
    assert warnings == []


def test_build_sites_warns_on_unattributed_and_variants():
    config = make_config()
    records = [record('10.1.0.0/24', site='LON-DC1'),
               record('10.3.0.0/24', site='lon-dc1  '),
               record('10.9.0.0/24', site='')]
    sites, warnings = build_sites(records, config)
    categories = [c for c, _, _ in warnings]

    assert len(sites) == 1
    assert 'unattributed_subnets' in categories
    assert 'site_name_variants' in categories


def test_build_sites_honours_the_site_filter():
    config = make_config(site_filter='LON-*')
    records = [record('10.1.0.0/24', site='LON-DC1'),
               record('10.2.0.0/24', site='NYC-BR2')]
    sites, _ = build_sites(records, config)
    assert [s.name for s in sites] == ['LON-DC1']


def test_build_sites_separates_classification_buckets():
    config = make_config()
    records = [record('10.1.0.0/24', site='LON-DC1'),
               record('10.1.1.0/30', site='LON-DC1')]
    sites, _ = build_sites(records, config)
    site = sites[0]

    assert site.networks('user_access') == ['10.1.0.0/24']
    assert site.networks('infrastructure') == ['10.1.1.0/30']


def test_build_sites_reports_unparseable_networks():
    config = make_config()
    sites, warnings = build_sites([record('not-a-network', site='LON-DC1')], config)
    assert sites[0].subnets == []
    assert 'unparseable_subnet' in [c for c, _, _ in warnings]


def test_find_cross_site_overlaps():
    from conftest import make_site
    overlaps = find_cross_site_overlaps([make_site('A', ('10.0.0.0/8',)),
                                         make_site('B', ('10.1.0.0/24',))])
    assert len(overlaps) == 1
    assert 'overlaps' in overlaps[0]


def test_extract_address_needs_the_required_triple():
    config = make_config()
    complete = [record('10.1.0.0/24', tags={'Address': '1 High St',
                                            'City': 'London',
                                            'Country': 'United Kingdom',
                                            'Postcode': 'EC1A 1AA'})]
    address = extract_address(complete, config)

    assert address['postal'] == {'address': '1 High St', 'city': 'London',
                                 'country': 'United Kingdom',
                                 'postal_code': 'EC1A 1AA'}
    assert address['source']['city'] == 'City'
    assert address['notes'] == []


def test_extract_address_drops_a_partial_address():
    config = make_config()
    partial = [record('10.1.0.0/24', tags={'City': 'London'})]
    address = extract_address(partial, config)

    assert address['postal'] == {}
    assert 'partial_address' in [c for c, _, _ in address['notes']]


def test_extract_address_reads_coordinates_and_validates_them():
    config = make_config()
    good = extract_address([record('10.1.0.0/24',
                                   tags={'Latitude': '51.5074',
                                         'Longitude': '-0.1278'})], config)
    assert (good['lat'], good['lon']) == (51.5074, -0.1278)
    assert good['source']['lat'] == 'Latitude'

    bad = extract_address([record('10.1.0.0/24',
                                  tags={'latitude': '999', 'longitude': '0'})],
                          config)
    assert bad['lat'] is None
    assert 'bad_coordinate' in [c for c, _, _ in bad['notes']]


def test_extract_address_ignores_a_lone_coordinate():
    config = make_config()
    address = extract_address([record('10.1.0.0/24', tags={'lat': '51.5'})], config)

    assert (address['lat'], address['lon']) == (None, None)
    assert 'partial_coordinates' in [c for c, _, _ in address['notes']]


def test_extract_address_takes_the_majority_and_warns():
    config = make_config()
    records = [record('10.1.0.0/24', tags={'address': 'A', 'city': 'London',
                                           'country': 'UK'}),
               record('10.1.1.0/24', tags={'address': 'A', 'city': 'London',
                                           'country': 'UK'}),
               record('10.1.2.0/24', tags={'address': 'B', 'city': 'London',
                                           'country': 'UK'})]
    address = extract_address(records, config)

    assert address['postal']['address'] == 'A'
    assert 'address_conflict' in [c for c, _, _ in address['notes']]


def test_build_sites_attaches_the_address_to_the_site():
    config = make_config()
    records = [record('10.1.0.0/24', site='LON-DC1',
                      tags={'Site': 'LON-DC1', 'address': '1 High St',
                            'city': 'London', 'country': 'UK',
                            'latitude': '51.5', 'longitude': '-0.12'})]
    sites, _ = build_sites(records, config)

    assert sites[0].postal['city'] == 'London'
    assert sites[0].lat == 51.5
    assert sites[0].address_source['lon'] == 'longitude'


def test_describe_reads_object_fields_then_tags():
    from ibx_kentik_prepop.sources.base import describe

    keys = ('description', 'comment', 'notes')

    assert describe({'description': 'Core switch'}, keys) == 'Core switch'
    assert describe({'comment': 'Spare'}, keys) == 'Spare'
    assert describe({}, keys, {'Notes': 'From an EA'}) == 'From an EA'
    assert describe({'description': {'value': 'EA shaped'}}, keys) == 'EA shaped'
    assert describe({}, keys) == ''
