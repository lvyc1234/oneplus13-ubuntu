#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Host-only interpretation of QCA vendor SAR attributes, never a transmitter.

Interpret a saved request; do not select limits, generate commands, open sockets
or claim firmware acknowledgement/effective radiated power. In particular,
USER half-dBm values and V2 BDF indexes are different namespaces.
"""
import argparse,json
from decimal import Decimal
from pathlib import Path

SELECTORS={'bdf0':0,'bdf1':1,'bdf2':2,'bdf3':3,'bdf4':4,'none':5,'user':6,'v2':7}


def uint(value,maximum=0xffffffff):
    if type(value) is not int or not 0<=value<=maximum:
        raise ValueError('Expected bounded unsigned integer')
    return value


def interpret(request):
    if not isinstance(request,dict) or set(request)!={'selection','rows'}:
        raise ValueError('Expected explicit selector and rows only')
    selection=request['selection']
    if not isinstance(selection,str) or selection not in SELECTORS:
        raise ValueError('Unknown QCA SAR selection')
    rows=request['rows']
    if not isinstance(rows,list) or len(rows)>64:raise ValueError('Invalid row count')
    result=[];identities=set()
    if selection not in ('user','v2') and rows:raise ValueError('BDF/disable selection does not accept user rows')
    if selection in ('user','v2') and not rows:raise ValueError('Explicit rows required; do not invent defaults')
    for row in rows:
        if not isinstance(row,dict):raise ValueError('Expected row object')
        value_key='power_half_dbm' if selection=='user' else 'power_index'
        if set(row)-{'band','chain','modulation',value_key} or value_key not in row:
            raise ValueError('Mixed or missing power/index namespace')
        value=uint(row[value_key],11 if selection=='v2' else 0xffffffff)
        options={}
        for key,allowed in [('band',(0,1)),('chain',(0,1)),('modulation',(0,1))]:
            if key in row:
                if type(row[key]) is not int or row[key] not in allowed:raise ValueError('Unreviewed band/chain/modulation value')
                options[key]=row[key]
        identity=tuple(options.get(key) for key in ('band','chain','modulation'))
        if identity in identities:raise ValueError('Duplicate row domain')
        identities.add(identity)
        # A missing selector applies to all supported values in the vendor
        # protocol. Refuse overlapping rows rather than guess their precedence.
        for prior in result:
            if all(key not in options or key not in prior['selectors'] or options[key]==prior['selectors'][key]
                   for key in ('band','chain','modulation')):
                raise ValueError('Overlapping row domains need explicit firmware semantics')
        item={'selectors':options,'omitted_selector_scope':'all supported values',value_key:value}
        if selection=='user':item['requested_limit_dbm']=str(Decimal(value)/2)
        else:item['bdf_profile_index']=value
        result.append(item)
    return {'scope':'SAVED_QCA_VENDOR_SAR_REQUEST_INTERPRETATION_ONLY','qca_selection':SELECTORS[selection],
        'selection':selection,'rows':result,'disable_requested':selection=='none',
        'firmware_acknowledgement_observed':False,'effective_transmit_power_dbm':None,
        'regulatory_or_phone_sar_policy_accepted':False,'hardware_radio_eligible':False,
        'phone_operations':0,'command_or_wire_payload_generated':False}


def main():
    p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args()
    if a.request.stat().st_size>65536:raise ValueError('Oversized saved request')
    result=interpret(json.loads(a.request.read_text()))
    a.report.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
