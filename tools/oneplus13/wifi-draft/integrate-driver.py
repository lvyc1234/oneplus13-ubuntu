#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Integrate the dormant phone SAR transport into one exact public kernel."""
import argparse,hashlib,json
from pathlib import Path
HERE=Path(__file__).resolve().parent

def integrate(kernel):
    lock=json.loads((HERE/'driver-sources.json').read_text())
    for name,expected in lock['files'].items():
        data=(kernel/name).read_bytes()
        if hashlib.sha256(data).hexdigest()!=expected:
            raise ValueError('Wrong/unreviewed kernel input: '+name)
    base=kernel/'drivers/net/wireless/ath/ath12k'
    def change(name,before,after):
        path=base/name;text=path.read_text()
        if text.count(before)!=1:raise ValueError('Non-unique integration anchor: '+name)
        path.write_text(text.replace(before,after),newline='\n')
    change('Makefile','ath12k-$(CONFIG_ATH12K_AHB)',
           'ath12k-y += phone-sar.o sar-wire.o\n\nath12k-$(CONFIG_ATH12K_AHB)')
    change('wmi.h','#include "cmn_defs.h"','#include "cmn_defs.h"\n#include "phone-sar.h"')
    change('wmi.h','struct ath12k_wmi_base {\n','struct ath12k_wmi_base {\n\tstruct ath12k_phone_sar phone_sar;\n')
    change('core.c','\tinit_completion(&ab->driver_recovery);',
           '\tath12k_phone_sar_init(ab);\n\tinit_completion(&ab->driver_recovery);')
    change('wmi.c','\tswitch (id) {\n\t\t/* Process all the WMI events here */',
           '\tif (ath12k_phone_sar_event(ab, id, skb->data, skb->len))\n\t\tgoto out;\n\n\tswitch (id) {\n\t\t/* Process all the WMI events here */')
    for suffix,ext2 in [('',False),('2',True)]:
        before='\tcase WMI_SERVICE_READY_EXT'+suffix+'_EVENTID:\n'
        after=before+'\t\tath12k_phone_sar_ready(ab, skb->data, skb->len, '+str(ext2).lower()+');\n'
        change('wmi.c',before,after)
    change('wmi.c','\tab->wmi_ab.preferred_hw_mode = WMI_HOST_HW_MODE_MAX;',
           '\tath12k_phone_sar_attach(ab);\n\tab->wmi_ab.preferred_hw_mode = WMI_HOST_HW_MODE_MAX;')
    change('wmi.c','void ath12k_wmi_detach(struct ath12k_base *ab)\n{\n\tint i;\n',
           'void ath12k_wmi_detach(struct ath12k_base *ab)\n{\n\tint i;\n\n\tath12k_phone_sar_detach(ab);\n')
    for name in ['phone-sar.c','phone-sar.h','sar-wire.c','sar-wire.h']:
        dest=base/name
        if dest.exists():raise ValueError('Preserve previous driver draft')
        dest.write_bytes((HERE/name).read_bytes())
    return {'scope':'DORMANT_PHONE_SAR_WMI_TRANSPORT_ONLY','kernel_pin':lock['kernel_pin'],
            'phone_operations':0,'cfg80211_callback_installed':False,'automatic_sar_set_or_get':False,
            'rf_admission_or_power_policy_installed':False,'candidate_modified':False,
            'files':{str(p.relative_to(kernel)):hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in [*[kernel/n for n in lock['files']],*[base/n for n in ['phone-sar.c','phone-sar.h','sar-wire.c','sar-wire.h']]]}}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--kernel',required=True,type=Path)
    a=p.parse_args();print(json.dumps(integrate(a.kernel.resolve()),indent=2))
