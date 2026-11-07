#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
from collections import deque
import contextlib
import errno
import io
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import Mock, patch


PATH = Path(__file__).resolve().parents[1] / "tools/oneplus13/ram-nfc-poll.py"
SPEC = importlib.util.spec_from_file_location("ram_nfc_poll", PATH)
POLL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLL)


class NfcNetlinkEncodingTests(unittest.TestCase):
    def test_family_multicast_groups_accept_linux_noflag_nesting(self):
        for flag in (0, 0x8000):
            with self.subTest(nested_flag=flag):
                group = POLL.attr_string(POLL.CTRL_ATTR_MCAST_GRP_NAME, "events")
                group += POLL.attr_u32(POLL.CTRL_ATTR_MCAST_GRP_ID, 37)
                attributes = POLL.attr(POLL.CTRL_ATTR_FAMILY_ID, struct.pack("<H", 42))
                attributes += POLL.attr(
                    POLL.CTRL_ATTR_MCAST_GROUPS | flag,
                    POLL.attr(1 | flag, group),
                )
                payload = struct.pack("<BBH", 1, 1, 0) + attributes
                netlink = POLL.NfcNetlink.__new__(POLL.NfcNetlink)
                netlink.send = Mock(return_value=1)
                netlink._receive_until = Mock(return_value=[(POLL.GENL_ID_CTRL, 0, payload)])
                self.assertEqual(netlink._family(), (42, 37))

    def test_constructor_subscribes_with_linux_netlink_socket_options(self):
        sock = Mock()
        with patch.object(POLL.Path, "is_file", return_value=True), patch.object(
            POLL.socket, "AF_NETLINK", 16, create=True
        ), patch.object(POLL.socket, "socket", return_value=sock), patch.object(
            POLL.NfcNetlink, "_family", return_value=(42, 37)
        ):
            netlink = POLL.NfcNetlink(100)
        sock.setsockopt.assert_called_once_with(270, 1, 37)
        self.assertEqual(netlink.events_group, 37)

    def test_alignment_and_u32_attribute(self):
        row = POLL.attr_u32(POLL.NFC_ATTR_DEVICE_INDEX, 7)
        self.assertEqual(len(row), 8)
        self.assertEqual(struct.unpack_from("<HH", row), (8, 1))
        self.assertEqual(struct.unpack_from("<I", row, 4)[0], 7)

    def test_rejects_truncated_and_zero_length_attributes(self):
        for row in (b"\x08\x00", b"\x00\x00\x00\x00"):
            with self.subTest(row=row), self.assertRaises(ValueError):
                POLL.parse_attrs(row)

    def test_protocol_mask_covers_expected_initiator_types(self):
        self.assertEqual(
            POLL.PROTOCOL_MASK,
            (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 6) | (1 << 7),
        )

    def test_message_headers_round_trip(self):
        packet = POLL.message(9, 0x22, 7, 1, POLL.NLM_F_REQUEST, ())
        rows = list(POLL.messages(packet))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:4], (0x22, POLL.NLM_F_REQUEST, 9, 0))
        self.assertEqual(rows[0][4][0], 7)

    def test_family_lookup_does_not_require_a_resolved_family(self):
        class Socket:
            def __init__(self):
                self.values = []

            def send(self, value):
                self.values.append(value)

        netlink = POLL.NfcNetlink.__new__(POLL.NfcNetlink)
        netlink.sock = Socket()
        netlink.sequence = 1
        netlink.send(POLL.CTRL_CMD_GETFAMILY)
        rows = list(POLL.messages(netlink.sock.values[0]))
        self.assertEqual(rows[0][0], POLL.GENL_ID_CTRL)


class NfcNetlinkRuntimeTests(unittest.TestCase):
    def netlink(self):
        netlink = POLL.NfcNetlink.__new__(POLL.NfcNetlink)
        netlink.sock = Mock()
        netlink.sequence = 1
        netlink.family = 42
        netlink.deadline = 100.0
        netlink.pending_events = deque()
        netlink.last_targets = []
        return netlink

    def event(self, index=0):
        payload = struct.pack('<BBH', POLL.NFC_EVENT_TARGETS_FOUND, 1, 0)
        payload += POLL.attr_u32(POLL.NFC_ATTR_DEVICE_INDEX, index)
        return (42, 0, 0, 0, payload)

    def test_wait_rechecks_targets_without_multicast(self):
        netlink = self.netlink()
        netlink.targets = Mock(side_effect=[[], [{'index': 3}]])
        clock = [0.0]

        def wait(*args):
            clock[0] += args[3]
            return [], [], []

        with patch.object(POLL.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(
            POLL.select, 'select', side_effect=wait
        ):
            self.assertTrue(netlink.wait_found(0, 3))
        self.assertEqual(netlink.targets.call_count, 2)
        self.assertEqual(netlink.last_targets, [{'index': 3}])
        self.assertLessEqual(clock[0], 1.0)

    def test_ack_does_not_drop_later_event_in_same_datagram(self):
        netlink = self.netlink()
        ack = (POLL.NLMSG_ERROR, 0, 1, 0, struct.pack('<i', 0))
        with patch.object(POLL.time, 'monotonic', return_value=0), patch.object(
            POLL.select, 'select', return_value=([netlink.sock], [], [])
        ), patch.object(POLL, 'recv_packets', return_value=[ack, self.event()]):
            self.assertEqual(netlink._receive_until(1, 5), [])
        self.assertEqual(len(netlink.pending_events), 1)

    def test_dump_does_not_drop_multicast(self):
        netlink = self.netlink()
        done = (POLL.NLMSG_DONE, 0, 1, 0, b'')
        with patch.object(POLL.time, 'monotonic', return_value=0), patch.object(
            POLL.select, 'select', return_value=([netlink.sock], [], [])
        ), patch.object(POLL, 'recv_packets', return_value=[self.event(), done]):
            self.assertEqual(netlink.dump(POLL.NFC_CMD_GET_DEVICE), [])
        self.assertEqual(len(netlink.pending_events), 1)

    def test_wait_consumes_cached_event_for_selected_device(self):
        netlink = self.netlink()
        netlink.pending_events.extend([self.event(9), self.event(0)])
        netlink.targets = Mock(return_value=[])
        with patch.object(POLL.time, 'monotonic', return_value=0):
            self.assertTrue(netlink.wait_found(0, 3))
        self.assertTrue(netlink.target_event_observed)

    def test_event_backlog_is_bounded_and_fails_closed(self):
        netlink = self.netlink()
        netlink.pending_events.extend([self.event()] * 128)
        with patch.object(POLL, 'recv_packets', return_value=[self.event()]):
            with self.assertRaisesRegex(RuntimeError, 'backlog'):
                netlink._sequence_packets(1)

    def test_data_without_ack_is_not_a_successful_request(self):
        netlink = self.netlink()
        payload = struct.pack('<BBH', 1, 1, 0)
        packet = (42, 0, 1, 0, payload)
        with patch.object(POLL.time, 'monotonic', side_effect=[0, 0, 0, 10]), patch.object(
            POLL.select, 'select', return_value=([netlink.sock], [], [])
        ), patch.object(POLL, 'recv_packets', return_value=[packet]):
            with self.assertRaises(TimeoutError):
                netlink._receive_until(1, 5)


class NfcCleanupTests(unittest.TestCase):
    def netlink(self, powered=False):
        netlink = Mock()
        netlink.devices.return_value = [{'index': 0, 'powered': powered}]
        return netlink

    def test_cleanup_requires_ack_and_selected_power_off_readback(self):
        result = POLL.cleanup(self.netlink(), 0, True)
        self.assertTrue(result['complete'])
        self.assertTrue(result['stop_poll_ack'])
        self.assertTrue(result['dev_down_ack'])

    def test_poll_auto_stop_einval_requires_power_off(self):
        for powered in (False, True):
            netlink = self.netlink(powered)
            netlink.request.side_effect = [OSError(errno.EINVAL, 'idle'), None]
            result = POLL.cleanup(netlink, 0, True)
            self.assertEqual(result['complete'], not powered)

    def test_already_down_ealready_requires_power_off(self):
        netlink = self.netlink()
        netlink.request.side_effect = [None, OSError(errno.EALREADY, 'off')]
        self.assertTrue(POLL.cleanup(netlink, 0, True)['complete'])

    def test_cleanup_does_not_swallow_busy_transport_or_parse_failures(self):
        for error in (OSError(errno.EBUSY, 'busy'), TimeoutError('late'), ValueError('bad')):
            for operation in (0, 1):
                with self.subTest(error=type(error).__name__, operation=operation):
                    netlink = self.netlink()
                    responses = [None, None]
                    responses[operation] = error
                    netlink.request.side_effect = responses
                    self.assertFalse(POLL.cleanup(netlink, 0, True)['complete'])
                    self.assertEqual(netlink.request.call_count, 2)

    def test_wrong_or_unknown_device_cannot_qualify_cleanup(self):
        for rows in ([{'index': 1, 'powered': False}], [], [{'index': 0}],
                     [{'index': 0, 'powered': False}] * 2):
            netlink = self.netlink()
            netlink.devices.return_value = rows
            self.assertFalse(POLL.cleanup(netlink, 0, True)['complete'])

    def test_cleanup_readback_timeout_is_failure(self):
        netlink = self.netlink()
        netlink.devices.side_effect = TimeoutError('late')
        self.assertFalse(POLL.cleanup(netlink, 0, True)['complete'])

    def run_main(self, netlink, arguments):
        output = io.StringIO()
        with patch.object(POLL, 'NfcNetlink', return_value=netlink), patch(
            'sys.argv', ['ram-nfc-poll.py', *arguments]
        ), contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            POLL.main()
        result = json.loads(output.getvalue().split('NFC_POLL_RESULT ', 1)[1])
        netlink.close.assert_called_once()
        return raised.exception.code, result

    def test_main_does_not_take_over_already_powered_controller(self):
        netlink = self.netlink(True)
        status, result = self.run_main(netlink, [])
        self.assertEqual(status, 1)
        self.assertFalse(result['pass'])
        netlink.request.assert_not_called()

    def test_main_qualifies_polled_target_without_inventing_multicast(self):
        netlink = self.netlink()
        netlink.devices.side_effect = [
            [{'index': 0, 'powered': False}], [{'index': 0, 'powered': True}],
            [{'index': 0, 'powered': False}],
        ]
        netlink.last_targets = [{'index': 3}]
        netlink.wait_found.return_value = True
        netlink.target_event_observed = False
        status, result = self.run_main(netlink, ['--wait-seconds', '3', '--require-target'])
        self.assertEqual(status, 0)
        self.assertTrue(result['target_found'])
        self.assertFalse(result['target_event_observed'])
        self.assertTrue(result['cleanup_complete'])

    def test_main_cleanup_failure_overrides_controller_success(self):
        netlink = self.netlink()
        netlink.devices.side_effect = [
            [{'index': 0, 'powered': False}], [{'index': 0, 'powered': True}],
            [{'index': 0, 'powered': True}],
        ]
        status, result = self.run_main(netlink, [])
        self.assertEqual(status, 1)
        self.assertFalse(result['pass'])
        self.assertFalse(result['cleanup_complete'])

    def test_arguments_reject_unbounded_wait_before_socket_creation(self):
        for args in (['--wait-seconds', '-1'], ['--wait-seconds', '121'], ['--device', '-1']):
            with patch.object(POLL, 'NfcNetlink') as constructor, patch(
                'sys.argv', ['ram-nfc-poll.py', *args]
            ), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                POLL.main()
            constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
