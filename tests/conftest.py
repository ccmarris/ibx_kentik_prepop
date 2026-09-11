#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
'''
Shared test fixtures for ibx_kentik_prepop
'''

from argparse import Namespace
import pytest
from ibx_kentik_prepop.config import build_config
from ibx_kentik_prepop.model import Site, SiteSubnet


def make_config(**overrides):
    '''
    Build a ProjectConfig without touching an ini or YAML file
    '''
    defaults = dict(source='uddi', site_key='Site', class_key='kentik_class',
                    site_type_key='site_type', max_prefix_len=None,
                    site_filter=None, include_address_blocks=False,
                    devices=False, use_insight=None, use_uai=None,
                    use_gateways=None, network_view=None, ip_space=None,
                    gm=None)
    defaults.update(overrides)
    return build_config(Namespace(**defaults), ini_file='does-not-exist.ini')


@pytest.fixture
def config():
    return make_config()


def record(cidr, site='LON-DC1', **kwargs):
    '''
    Build a normalised source subnet record
    '''
    data = {'cidr': cidr, 'site': site, 'class_override': '', 'site_type': '',
            'name': '', 'comment': '', 'gateways': [], 'source_id': '', 'tags': {}}
    data.update(kwargs)
    return data


def make_site(name='LON-DC1', cidrs=('10.1.0.0/24',), classification='user_access'):
    '''
    Build a Site with the given summarised subnets
    '''
    subnets = [SiteSubnet(cidr=c, classification=classification, source_cidrs=(c,))
               for c in cidrs]
    return Site(name=name, subnets=subnets)
