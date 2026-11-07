"""Offline fake-process checks, no device/network operations."""
import contextlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('wifi', Path(__file__).resolve().parents[1]/'tools/oneplus13/automatic-wifi-check.py')
wifi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wifi)


class Fake:
    def __init__(self, fail=None, target=True, state='30 (disconnected)',fail_status=124):
        self.calls = []
        self.ident = None
        self.fail = fail
        self.target = target
        self.password_file = None
        self.state=state
        self.fail_status=fail_status
        self.scan_reads=0

    def __call__(self, args, seconds):
        self.calls.append(args)
        assert 0 < seconds <= 18
        if self.fail and self.fail in args:
            return self.fail_status, 'private diagnostics not printed'
        if 'SSID' in args:
            return 0, 'offline-network\n' if self.target else 'other-network\n'
        if 'GENERAL.STATE' in args:
            return 0, self.state+'\n'
        if 'GENERAL.DBUS-PATH' in args:
            return 0,'/org/freedesktop/NetworkManager/Devices/3\n'
        if 'LastScan' in args:
            self.scan_reads+=1
            return 0,'x '+str(100 if self.scan_reads==1 else 200)+'\n'
        if 'add' in args:
            self.ident = args[args.index('connection.uuid')+1]
        if 'passwd-file' in args:
            self.password_file = Path(args[-1])
            if os.name == 'posix':
                assert self.password_file.stat().st_mode & 0o777 == 0o600
            assert self.password_file.read_text() == '802-11-wireless-security.psk:offline-secret-value\n'
        if 'GENERAL.CON-UUID' in args:
            return 0, self.ident+'\n'
        if 'address' in args:
            return 0, json.dumps([{'addr_info': [{'scope': 'global'}]}])
        if 'route' in args:
            return 0, json.dumps([{'dst': 'default', 'gateway': '192.0.2.1'}])
        if args[0]=='curl':
            return 0,'http_code=200\nssl_verify_result=0\n'
        if args[0]=='iw' and args[-1]=='link':
            return 0, 'Connected to 02:00:00:00:00:01\n\tfreq: 5180\n'
        return 0, ''


class Checks(unittest.TestCase):
    def test_installed_guard_checks_history_before_each_command(self):
        from types import SimpleNamespace
        good=SimpleNamespace(returncode=0,stdout=b'ath12k initialized\n')
        bad=SimpleNamespace(returncode=0,stdout=b'ath12k Hardware restart was requested\n')
        with patch.object(wifi.subprocess,'run',side_effect=[good,bad]) as history, \
                patch.object(wifi,'run',return_value=(0,'')) as command, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wifi.guarded_run(['nmcli','device'],4),(0,''))
            with self.assertRaisesRegex(ValueError,'history fault'):
                wifi.guarded_run(['nmcli','device'],4)
        self.assertEqual(history.call_count,2)
        command.assert_called_once_with(['nmcli','device'],4)

    def test_installed_guard_refuses_unavailable_or_oversized_history(self):
        from types import SimpleNamespace
        for result in [SimpleNamespace(returncode=1,stdout=b''),
                       SimpleNamespace(returncode=0,stdout=b'x'*(4*1024*1024+1))]:
            with self.subTest(returncode=result.returncode), \
                    patch.object(wifi.subprocess,'run',return_value=result), \
                    patch.object(wifi,'run') as command:
                with self.assertRaisesRegex(ValueError,'unavailable'):
                    wifi.guarded_run(['nmcli','device'],4)
                command.assert_not_called()

    def test_country_transition_is_held_before_any_command(self):
        fake=Fake()
        with self.assertRaisesRegex(ValueError,'held'):
            self.call(fake,{'ssid':'offline-network','password':'offline-secret-value','stock_country_code':'CN'})
        self.assertEqual(fake.calls,[])

    def call(self, fake, credentials=None, existing_link=False):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()) as output, patch.object(wifi.time, 'sleep'):
            result = wifi.check(credentials or {'ssid': 'offline-network', 'password': 'offline-secret-value', 'external_diagnostics': True},
                                'wlan-test', execute=fake, runtime=Path(d), existing_link=existing_link)
            self.assertNotIn('offline-secret-value', output.getvalue())
            self.assertNotIn('offline-secret-value', repr(fake.calls))
            self.assertFalse(list(Path(d).iterdir()))
        return result

    def test_full_success_requires_target_profile_dhcp_http(self):
        r = self.call(Fake())
        self.assertTrue(all(r[k] for k in ['association_pass', 'dhcp_pass', 'http_pass', 'https_pass', 'automatic_acceptance_pass']))

    def existing_fake(self, configured='offline-network', linked='offline-network', frequency=5180):
        base=Fake(state='100 (connected)')
        base.ident='00000000-1111-2222-3333-444444444444'
        def execute(args,seconds):
            if '802-11-wireless.ssid' in args:
                base.calls.append(args)
                return 0,configured+'\n'
            if args[0]=='iw':
                base.calls.append(args)
                return 0,'Connected to 02:00:00:00:00:01\n\tSSID: '+linked+'\n\tfreq: '+str(frequency)+'\n'
            return base(args,seconds)
        execute.calls=base.calls
        return execute

    def test_existing_link_checks_real_band_and_traffic_without_mutation(self):
        fake=self.existing_fake()
        r=self.call(fake,{'ssid':'offline-network','password':'offline-secret-value','band':'a',
                          'external_diagnostics':True},existing_link=True)
        self.assertTrue(r['automatic_acceptance_pass'] and r['https_pass'])
        self.assertTrue(r['active_link_ssid_matches'])
        self.assertEqual(r['link_frequency_mhz'],5180)
        self.assertEqual(r['scope'],'ONE_NETWORK_EXISTING_LINK_RAM_ONLY')
        self.assertFalse(r['directed_scan_requested'] or r['connection_reactivation_requested'])
        self.assertFalse(any(any(token in c for token in ['rescan','add','up','managed','passwd-file']) for c in fake.calls))
        self.assertNotIn('offline-network',json.dumps(r))

    def test_existing_link_rejects_wrong_configured_or_actual_network(self):
        for configured,linked in [('other','offline-network'),('offline-network','other')]:
            with self.subTest(configured=configured,linked=linked):
                fake=self.existing_fake(configured=configured,linked=linked)
                r=self.call(fake,existing_link=True)
                self.assertFalse(r['association_pass'] or r['automatic_acceptance_pass'])
                self.assertFalse(any(c[0]=='curl' for c in fake.calls))

    def test_existing_link_rejects_wrong_band_or_disconnected_state(self):
        fake=self.existing_fake(frequency=2437)
        r=self.call(fake,{'ssid':'offline-network','password':'offline-secret-value','band':'a'},existing_link=True)
        self.assertFalse(r['band_pass'] or r['automatic_acceptance_pass'])
        disconnected=Fake()
        r=self.call(disconnected,existing_link=True)
        self.assertFalse(r['association_pass'] or r['automatic_acceptance_pass'])
        self.assertFalse(any('rescan' in c or 'up' in c for c in disconnected.calls))

    def test_associated_scan_has_one_request_and_longer_bounded_completion_wait(self):
        base=Fake(state='100 (connected)');observations=0
        def delayed(args,seconds):
            nonlocal observations
            if 'LastScan' in args:
                observations+=1
                return 0,'x '+str(100 if observations<13 else 200)+'\n'
            return base(args,seconds)
        delayed.calls=base.calls
        r=self.call(delayed)
        self.assertTrue(r['scan_completed'] and r['automatic_acceptance_pass'])
        self.assertEqual(r['scan_wait_budget_seconds'],45)
        self.assertEqual(r['scan_observations'],12)
        self.assertEqual(sum('rescan' in c for c in base.calls),1)

    def test_5ghz_profile_and_actual_frequency_are_both_required(self):
        fake=Fake()
        result=self.call(fake,{'ssid':'offline-network','password':'offline-secret-value','band':'a'})
        self.assertEqual(result['associated_band'],'5GHz')
        self.assertEqual(result['link_frequency_mhz'],5180)
        self.assertTrue(result['band_pass'] and result['automatic_acceptance_pass'])
        profile=next(c for c in fake.calls if 'add' in c)
        self.assertEqual(profile[-2:],['802-11-wireless.band','a'])
        self.assertFalse(any(c[0] in ('ping','getent') or 'https://example.com/' in c for c in fake.calls))
        self.assertNotIn('02:00:00:00:00:01',json.dumps(result))

    def test_wrong_or_unproven_frequency_never_reaches_traffic_acceptance(self):
        for output in ('freq: 2437\n','freq: 5975\n','Not connected.\n','freq: 5180\nfreq: 5240\n','freq: private\n'):
            base=Fake()
            def link(args,seconds):
                if args[0]=='iw': return 0,output
                return base(args,seconds)
            link.calls=base.calls
            with self.subTest(output=output):
                result=self.call(link,{'ssid':'offline-network','password':'offline-secret-value','band':'a'})
                self.assertFalse(result['band_pass'] or result['http_pass'])
                self.assertFalse(any(c[0]=='curl' for c in base.calls))

    def test_24ghz_is_preserved_with_explicit_band(self):
        base=Fake()
        def link(args,seconds):
            if args[0]=='iw':return 0,'freq: 2437\n'
            return base(args,seconds)
        link.calls=base.calls
        result=self.call(link,{'ssid':'offline-network','password':'offline-secret-value','band':'bg'})
        self.assertTrue(result['band_pass'] and result['automatic_acceptance_pass'])
        self.assertEqual(result['associated_band'],'2.4GHz')

    def test_iw_decimal_whole_mhz_link_output_is_supported(self):
        base=Fake()
        def link(args,seconds):
            if args[0]=='iw':return 0,'freq: 5180.0\n'
            return base(args,seconds)
        link.calls=base.calls
        result=self.call(link,{'ssid':'offline-network','password':'offline-secret-value','band':'a'})
        self.assertTrue(result['band_pass'] and result['automatic_acceptance_pass'])
        self.assertEqual(result['link_frequency_mhz'],5180)

    def test_link_query_failure_cannot_pass(self):
        result=self.call(Fake(fail='iw',fail_status=127))
        self.assertFalse(result['band_pass'] or result['http_pass'])

    def test_invalid_band_rejected_before_any_network_command(self):
        for band in ('5','6GHz',False,42):
            fake=Fake()
            with self.assertRaises(ValueError):
                self.call(fake,{'ssid':'offline-network','password':'offline-secret-value','band':band})
            self.assertEqual(fake.calls,[])

    def test_ipv6_first_activation_waits_for_delayed_ipv4_on_same_connection(self):
        base=Fake();observations=0
        def delayed(args,seconds):
            nonlocal observations
            if 'address' in args:
                observations+=1
                if observations<3:return 0,'[]'
            if 'route' in args and observations<3:return 0,'[]'
            return base(args,seconds)
        delayed.calls=base.calls
        r=self.call(delayed)
        self.assertTrue(r['dhcp_pass'] and r['http_pass'] and r['https_pass'])
        self.assertEqual(r['ipv4_observations'],3)
        self.assertEqual(sum('up' in c for c in base.calls),1)

    def test_ipv6_only_never_passes_ipv4_acceptance_and_observation_is_bounded(self):
        base=Fake();observations=0
        def absent(args,seconds):
            nonlocal observations
            if 'address' in args:observations+=1;return 0,'[]'
            if 'route' in args:return 0,'[]'
            return base(args,seconds)
        absent.calls=base.calls
        r=self.call(absent)
        self.assertFalse(r['dhcp_pass'] or r['http_pass'] or r['https_pass'])
        self.assertEqual(observations,8)
        self.assertFalse(any(c[0]=='curl' for c in base.calls))

    def test_ipv4_query_timeout_stops_without_reconnection(self):
        f=Fake(fail='address');r=self.call(f)
        self.assertFalse(r['dhcp_pass'] or r['http_pass'] or r['https_pass'])
        self.assertEqual(r['ipv4_address_rc'],124)
        self.assertEqual(sum('up' in c for c in f.calls),1)

    def test_async_scan_waits_for_completion_before_listing(self):
        base=Fake();observations=0
        def delayed(args,seconds):
            nonlocal observations
            if 'LastScan' in args:
                observations+=1
                return 0,'x '+str(100 if observations<4 else 200)+'\n'
            if 'SSID' in args:self.assertEqual(observations,4)
            return base(args,seconds)
        delayed.calls=base.calls
        r=self.call(delayed)
        self.assertTrue(r['scan_completed'] and r['http_pass'] and r['https_pass'])
        self.assertEqual(r['scan_observations'],3)
        self.assertEqual(sum('rescan' in c for c in base.calls),1)

    def test_scan_never_finishes_does_not_list_or_authenticate(self):
        base=Fake();observations=0
        def unchanged(args,seconds):
            nonlocal observations
            if 'LastScan' in args:observations+=1;return 0,'x 100\n'
            return base(args,seconds)
        unchanged.calls=base.calls
        r=self.call(unchanged)
        self.assertFalse(r['scan_completed'] or r['association_pass'])
        self.assertEqual(observations,10)
        self.assertFalse(any('SSID' in c or 'passwd-file' in c for c in base.calls))

    def test_invalid_scan_property_never_stages_credentials(self):
        base=Fake()
        def invalid(args,seconds):
            if 'LastScan' in args:return 0,'x private-invalid\n'
            return base(args,seconds)
        invalid.calls=base.calls
        r=self.call(invalid)
        self.assertTrue(r['scan_property_unavailable'])
        self.assertFalse(any('rescan' in c or 'passwd-file' in c for c in base.calls))

    def test_curl_zero_requires_expected_http_and_certificate_metrics(self):
        for output in ['', 'http_code=302\nssl_verify_result=0\n','http_code=200\nssl_verify_result=9\n']:
            base=Fake()
            def incomplete(args,seconds):
                if args[0]=='curl':return 0,output
                return base(args,seconds)
            incomplete.calls=base.calls
            self.assertFalse(self.call(incomplete)['https_pass'])

    def test_nm_timeout_stops_before_scan_auth(self):
        f = Fake(fail='GENERAL.STATE')
        r = self.call(f)
        self.assertEqual(len(f.calls), 2)
        self.assertFalse(r['association_pass'])

    def test_target_absent_never_stages_secret_or_connects(self):
        f = Fake(target=False)
        r = self.call(f)
        self.assertFalse(r['target_seen'])
        self.assertFalse(any('passwd-file' in c for c in f.calls))
        self.assertEqual(r['unique_visible_ssids'],1)
        self.assertNotIn('other-network',json.dumps(r))

    def test_scan_counts_distinguish_empty_results_without_publishing_names(self):
        base=Fake(target=False)
        def empty(args,seconds):
            if 'SSID' in args:return 0,''
            return base(args,seconds)
        empty.calls=base.calls
        r=self.call(empty)
        self.assertEqual(r['scan_result_rows'],0)
        self.assertEqual(r['visible_ssid_rows'],0)
        self.assertFalse(r['target_seen'])
        self.assertFalse(any('passwd-file' in c for c in base.calls))

    def test_one_directed_scan_and_delayed_cache_publish(self):
        base=Fake();lists=0
        def delayed(args,seconds):
            nonlocal lists
            if 'SSID' in args:
                lists+=1
                if lists<3:return 0,''
            return base(args,seconds)
        delayed.calls=base.calls
        r=self.call(delayed)
        self.assertTrue(r['target_seen'] and r['http_pass'] and r['https_pass'])
        self.assertEqual(r['ap_list_observations'],3)
        scans=[c for c in base.calls if 'rescan' in c]
        self.assertEqual(len(scans),1)
        self.assertEqual(scans[0][-2:],['ssid','offline-network'])

    def test_cache_timeout_stops_without_new_scan_or_credentials(self):
        base=Fake(fail='SSID');r=self.call(base)
        self.assertEqual(r['ap_list_rc'],124)
        self.assertEqual(r['ap_list_observations'],1)
        self.assertEqual(sum('rescan' in c for c in base.calls),1)
        self.assertFalse(any('passwd-file' in c for c in base.calls))

    def test_auth_timeout_removes_secret_file_and_stops(self):
        f = Fake(fail='up')
        r = self.call(f)
        self.assertFalse(r['association_pass'])
        self.assertFalse(f.password_file and f.password_file.exists())

    def test_failed_https_not_a_pass(self):
        r = self.call(Fake(fail='curl'))
        self.assertTrue(r['association_pass'] and r['dhcp_pass'])
        self.assertFalse(r['https_pass'])

    def test_missing_supplicant_never_scans_or_authenticates(self):
        f=Fake(fail='busctl')
        result=self.call(f)
        self.assertEqual(len(f.calls),1)
        self.assertFalse(result['association_pass'])

    def test_unavailable_device_does_not_scan(self):
        base=Fake(state='20 (unavailable)')
        result=self.call(base)
        self.assertFalse(result['nm_device_ready'])
        self.assertFalse(any('rescan' in c for c in base.calls))

    def test_subprocess_timeout_is_bounded_and_discards_output(self):
        from unittest.mock import patch
        import subprocess
        with patch.object(wifi.subprocess, 'run', side_effect=subprocess.TimeoutExpired('test', 1, output='private')):
            self.assertEqual(wifi.run(['no-real-command'], 1), (124, ''))

    def test_real_missing_executable_returns_code_without_traceback(self):
        self.assertEqual(wifi.run(['/oneplus13-offline-no-such-binary'],1),(127,'executable missing'))

    def test_missing_optional_ping_does_not_abort_https(self):
        f=Fake(fail='ping',fail_status=127)
        r=self.call(f)
        self.assertEqual(r['gateway_rc'],127)
        self.assertEqual(r['gateway_error_class'],'executable_missing')
        self.assertTrue(r['gateway_probe_skipped'])
        self.assertTrue(any(c[0]=='getent' for c in f.calls))
        self.assertTrue(any(c[0]=='curl' for c in f.calls))
        self.assertTrue(r['association_pass'] and r['dhcp_pass'] and r['http_pass'] and r['https_pass'])

    def test_missing_curl_is_reported_as_failure(self):
        r=self.call(Fake(fail='curl',fail_status=127))
        self.assertFalse(r['https_pass'])
        self.assertEqual(r['https_error_class'],'executable_missing')

    def test_missing_optional_dns_probe_still_runs_interface_bound_https(self):
        f=Fake(fail='getent',fail_status=127)
        r=self.call(f)
        self.assertTrue(r['dns_probe_skipped'] and r['https_pass'])
        curl=next(c for c in f.calls if c[0]=='curl' and 'https://example.com/' in c)
        self.assertEqual(curl[curl.index('--interface')+1],'wlan-test')
        self.assertEqual(curl[curl.index('--noproxy')+1],'*')
        self.assertIn('https://example.com/',curl)
        self.assertNotIn('--insecure',curl)
        self.assertIn('--ipv4',curl)

    def test_http_success_accepts_network_when_https_times_out(self):
        base=Fake()
        def split(args,seconds):
            if args[0]=='curl':
                if 'https://example.com/' in args:
                    return 28,'dns_s=0.01\ntcp_s=0\ntls_s=0\nfirst_byte_s=0\ntotal_s=4\nhttp_code=000\n'
                return 0,'dns_s=0.01\ntcp_s=0.1\nfirst_byte_s=0.2\ntotal_s=0.2\nhttp_code=200\n'
            return base(args,seconds)
        split.calls=base.calls
        r=self.call(split)
        self.assertTrue(r['http_pass'] and r['automatic_acceptance_pass'])
        self.assertFalse(r['https_pass'])
        self.assertEqual(r['https_error_class'],'transfer_timeout')

    def test_invalid_http_endpoint_rejected_before_authentication(self):
        f=Fake()
        with self.assertRaises(ValueError):
            wifi.check({'ssid':'offline-network','password':'offline-secret-value',
                        'http_probe_url':'https://private.example/'},'wlan-test',execute=f,runtime=Path(tempfile.gettempdir()))
        self.assertEqual(f.calls,[])

    def test_missing_ping_and_https_failure_never_passes(self):
        base=Fake()
        def both(args,seconds):
            if args[0]=='ping':return 127,'private'
            if args[0]=='curl':return 7,'private'
            return base(args,seconds)
        both.calls=base.calls
        r=self.call(both)
        self.assertTrue(r['gateway_probe_skipped'])
        self.assertFalse(r['https_pass'])

    def test_missing_core_tool_preflight_stops_before_network_commands(self):
        for missing in ['nmcli','busctl','ip','curl','iw']:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as d:
                credentials=Path(d)/'credentials.json'
                credentials.write_text(json.dumps({'ssid':'offline-network','password':'offline-secret-value'}))
                with patch.object(sys, 'argv',
                                  ['automatic-wifi-check.py','--credentials',str(credentials)]), \
                     patch.object(wifi.Path,'read_text', side_effect=['x / overlay rw 0 0',credentials.read_text()]), \
                     patch.object(wifi.Path,'iterdir',return_value=iter([])), \
                     patch.object(wifi.shutil,'which',side_effect=lambda name:None if name==missing else '/usr/bin/'+name), \
                     patch.object(wifi,'check') as check, contextlib.redirect_stdout(io.StringIO()) as output:
                    wifi.main()
                check.assert_not_called()
                result=json.loads(output.getvalue().split('AUTO_WIFI_RESULT ',1)[1])
                self.assertEqual(result['missing_required_commands'],[missing])
                self.assertFalse(result['automatic_acceptance_pass'])
                self.assertFalse(credentials.exists())

    def test_permission_error_is_sanitized(self):
        with patch.object(wifi.subprocess,'run',side_effect=PermissionError('private diagnostics')):
            self.assertEqual(wifi.run(['unused'],1),(126,'executable unavailable'))

    def test_curl_failure_preserves_metrics_and_discards_raw_stderr(self):
        from types import SimpleNamespace
        completed=SimpleNamespace(returncode=28,stdout='dns_s=0.01\ntcp_s=0\n',stderr='private endpoint details')
        with patch.object(wifi.subprocess,'run',return_value=completed):
            self.assertEqual(wifi.run(['curl','--write-out',wifi.HTTPS_WRITE_OUT],9),
                             (28,'dns_s=0.01\ntcp_s=0\n'))

    def test_timing_parser_keeps_only_bounded_numeric_fields(self):
        r=wifi.https_metrics('dns_s=0.01\ntcp_s=0.2\ntls_s=0.4\nfirst_byte_s=0.6\ntotal_s=0.7\nhttp_code=200\nremote_ip=private\n')
        self.assertEqual(r,dict(dns_s=0.01,tcp_s=0.2,tls_s=0.4,first_byte_s=0.6,total_s=0.7,http_code=200))
        self.assertEqual(wifi.https_metrics('dns_s=nan\ntcp_s=-1\ntls_s=inf\ntotal_s=999\nhttp_code=private\n'),{})

    def test_https_timeout_keeps_completed_stage_timings_without_passing(self):
        base=Fake()
        def timeout(args,seconds):
            if args[0]=='curl':
                return 28,'dns_s=0.01\ntcp_s=0.1\ntls_s=0\nfirst_byte_s=0\ntotal_s=4.0\nhttp_code=000\n'
            return base(args,seconds)
        timeout.calls=base.calls
        r=self.call(timeout)
        self.assertFalse(r['https_pass'])
        self.assertEqual(r['https_error_class'],'transfer_timeout')
        self.assertEqual(r['https_metrics']['tcp_s'],0.1)
        self.assertEqual(r['https_metrics']['tls_s'],0)
        self.assertEqual(r['https_address_family'],'IPv4')

    def test_ssl_verification_result_is_numeric_and_bounded(self):
        self.assertEqual(wifi.https_metrics('ssl_verify_result=9\n'),{'ssl_verify_result':9})
        self.assertEqual(wifi.https_metrics('ssl_verify_result=private\n'),{})
        self.assertEqual(wifi.https_metrics('ssl_verify_result=-1\n'),{})

    def test_certificate_error_never_passes(self):
        r=self.call(Fake(fail='curl',fail_status=60))
        self.assertFalse(r['https_pass'])
        self.assertEqual(r['https_error_class'],'certificate_verification_failed')


if __name__ == '__main__':
    unittest.main()
