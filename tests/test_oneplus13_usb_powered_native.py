# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('powered',ROOT/'tools/oneplus13/ram-usb-powered-native-check.py')
P=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(P)
N=P.load_native()


class PoweredTests(unittest.TestCase):
    def fixture(self, *, candidates=True):
        n=Mock(wraps=N)
        n.ZERO=N.ZERO
        n.unused.return_value=dict(status=dict(N.IDLE),values=dict(N.ZERO))
        n.host_guard.return_value=None
        n.devices.return_value=[dict(path='1-1')]
        n.block_candidates.return_value=[dict(device='/dev/sdb1')] if candidates else []
        u=Mock()
        u.firmware_lease_status.side_effect=[dict(P.ACTIVE),dict(P.ACTIVE),dict(P.ACTIVE),dict(P.ACTIVE),dict(P.RESTORED),dict(P.RESTORED)]
        u.firmware_otg_inventory.side_effect=[dict(complete=True,values=dict(P.ONE)),dict(complete=True,values=dict(N.ZERO))]
        u.firmware_lease_restore.return_value=dict(status=dict(P.RESTORED),cleanup_complete=True)
        u.kernel_diagnostics.return_value=dict(available=False)
        proof=dict(read_bytes=4096,descriptor_read_only=True,block_identity_verified=True,cleanup_complete=True)
        child=Mock(return_value=Mock(returncode=0,stdout=json.dumps(proof)))
        return n,u,child

    def probe(self,n,u,child,clock=None):
        with patch.object(P.time,'monotonic',side_effect=clock or list(range(0,500,1))),patch.object(P.time,'sleep'):
            result=P.probe(n,u,Path('/unused'),60,child)
        u.write.assert_not_called();u.reconcile_host.assert_not_called()
        self.assertEqual(u.firmware_lease_command.call_args_list[0].args,('start',))
        self.assertEqual(sum(c.args==('start',) for c in u.firmware_lease_command.call_args_list),1)
        self.assertLessEqual(sum(c.args==('restore',) for c in u.firmware_lease_command.call_args_list),1)
        return result

    def test_powered_success_restores_existing_lease_and_has_distinct_scope(self):
        n,u,c=self.fixture();r=self.probe(n,u,c)
        self.assertTrue(r['functional_pass']);self.assertTrue(r['cleanup_complete'])
        self.assertEqual(r['scope'],'ONEPLUS13_RAM_POWERED_NATIVE_USB_READ_ONLY')
        self.assertFalse(r['role_write_requested']);self.assertNotIn('role_restored',r)
        self.assertEqual(c.call_args.kwargs['timeout'],8)
        self.assertEqual([c.args[0] for c in u.firmware_lease_command.call_args_list],['start','restore'])
        u.firmware_lease_restore.assert_not_called()

    def test_no_enumeration_has_clean_failure(self):
        n,u,c=self.fixture(candidates=False)
        n.devices.return_value=[]
        u.firmware_lease_status.side_effect=lambda:dict(P.ACTIVE)
        u.firmware_lease_command.side_effect=lambda command: setattr(u.firmware_lease_status,'side_effect',lambda:dict(P.RESTORED)) if command=='restore' else None
        r=self.probe(n,u,c,clock=[0,0,0,0,0,0,100,100,100,100])
        self.assertFalse(r['functional_pass']);self.assertTrue(r['cleanup_complete']);c.assert_not_called()

    def test_role_loss_after_active_restores_without_reassertion(self):
        n,u,c=self.fixture();n.host_guard.side_effect=[None,None,RuntimeError('lost')]
        u.firmware_lease_status.side_effect=[dict(P.ACTIVE),dict(P.ACTIVE),dict(P.ACTIVE),dict(P.RESTORED),dict(P.RESTORED)]
        r=self.probe(n,u,c);self.assertFalse(r['functional_pass']);self.assertTrue(r['cleanup_complete']);c.assert_not_called()

    def test_start_timeout_does_not_retry_and_unknown_remains_unknown(self):
        n,u,c=self.fixture();u.firmware_lease_command.side_effect=subprocess.TimeoutExpired('start',20)
        u.firmware_lease_status.side_effect=lambda:dict(state=3,used=1,dirty_ap=1,dirty_switch=0)
        r=self.probe(n,u,c);self.assertTrue(r['firmware_residual_unknown']);self.assertFalse(r['cleanup_complete']);c.assert_not_called()

    def test_service_loss_during_poll_stops_read_and_unknown(self):
        n,u,c=self.fixture();u.firmware_lease_status.side_effect=[dict(P.ACTIVE),RuntimeError('service lost'),dict(state=3,used=1,dirty_ap=1,dirty_switch=0)]
        r=self.probe(n,u,c);self.assertFalse(r['cleanup_complete']);self.assertFalse(r['functional_pass']);c.assert_not_called()

    def test_child_failures_never_qualify_and_still_restore(self):
        for child in [Mock(side_effect=subprocess.TimeoutExpired('read',8)),Mock(return_value=Mock(returncode=1,stdout='')),Mock(return_value=Mock(returncode=0,stdout='{}')),Mock(return_value=Mock(returncode=0,stdout='{"read_bytes":4096,"read_bytes":0}'))]:
            n,u,_=self.fixture();r=self.probe(n,u,child)
            self.assertTrue(r['cleanup_complete']);self.assertFalse(r['functional_pass'])

    def test_false_or_idle_terminal_after_active_is_unproven(self):
        for terminal in [dict(N.IDLE),dict(P.REFUSED),dict(P.RESTORED,used=True),dict(P.RESTORED,dirty_ap=1)]:
            n,u,c=self.fixture();u.firmware_lease_status.side_effect=[dict(P.ACTIVE),dict(P.ACTIVE),dict(P.ACTIVE),terminal]
            r=self.probe(n,u,c);self.assertFalse(r['cleanup_complete']);self.assertTrue(r['firmware_residual_unknown'])

    def test_final_pair_must_be_independent_exact_zero(self):
        for pair in [dict(P.ONE),dict(N.ZERO,oneplus13_otg_switch=False)]:
            n,u,c=self.fixture();u.firmware_otg_inventory.side_effect=[dict(complete=True,values=dict(P.ONE)),dict(complete=True,values=pair)]
            r=self.probe(n,u,c);self.assertFalse(r['cleanup_complete'])

    def test_pair_failure_after_active_never_accepts_refused_terminal(self):
        for terminal in [dict(P.REFUSED),dict(P.RESTORED)]:
            n,u,c=self.fixture()
            u.firmware_lease_status.side_effect=[dict(P.ACTIVE),dict(P.ACTIVE),terminal,terminal]
            u.firmware_otg_inventory.side_effect=[dict(complete=True,values=dict(N.ZERO)),dict(complete=True,values=dict(N.ZERO))]
            r=self.probe(n,u,c);c.assert_not_called()
            self.assertEqual(r['cleanup_complete'],terminal==P.RESTORED)
            self.assertFalse(r['functional_pass'])

    def test_start_ack_timeout_then_active_requires_restored_and_no_retry(self):
        for terminal in [dict(P.REFUSED),dict(P.RESTORED)]:
            n,u,c=self.fixture()
            u.firmware_lease_command.side_effect=lambda command: (_ for _ in ()).throw(subprocess.TimeoutExpired('start',20)) if command=='start' else None
            u.firmware_lease_status.side_effect=[dict(P.ACTIVE),terminal,terminal]
            u.firmware_otg_inventory.side_effect=[dict(complete=True,values=dict(N.ZERO))]
            r=self.probe(n,u,c)
            self.assertEqual(r['cleanup_complete'],terminal==P.RESTORED)
            self.assertEqual([x.args[0] for x in u.firmware_lease_command.call_args_list],['start','restore'])
            self.assertFalse(r['functional_pass']);c.assert_not_called()

    def test_baseline_or_initial_host_failure_never_starts(self):
        for field in ['unused','host_guard']:
            n,u,c=self.fixture();getattr(n,field).side_effect=RuntimeError('reject')
            r=P.probe(n,u,Path('/unused'),60,c)
            u.firmware_lease_command.assert_not_called();u.firmware_lease_restore.assert_not_called();c.assert_not_called()
            self.assertFalse(r['functional_pass'])

    def test_read_child_rechecks_active_and_host_before_and_after(self):
        n,u,c=self.fixture();u.firmware_lease_status.side_effect=[dict(P.ACTIVE),dict(P.ACTIVE)]
        n.read_block.return_value={'read_bytes':4096}
        self.assertEqual(P.child_read(n,u,Path('/mode'),{}),{'read_bytes':4096})
        self.assertEqual(n.host_guard.call_count,2);self.assertEqual(u.firmware_lease_status.call_count,2)
        u.write.assert_not_called();u.firmware_lease_command.assert_not_called()

    def test_work_budget_shortens_wait_and_never_extends_kernel_watchdog(self):
        n,u,c=self.fixture();r=self.probe(n,u,c,clock=[0,0,30,30,30,30,31,31,31])
        self.assertEqual(r['wait_budget_seconds'],34)
        self.assertTrue(r['cleanup_complete'])


if __name__=='__main__':unittest.main()
