#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock


PATH = (
    Path(__file__).resolve().parents[1]
    / "tools/oneplus13/ram-usb-role-check.py"
)
SPEC = importlib.util.spec_from_file_location("ram_usb_role", PATH)
USB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(USB)


class UsbRolePathTests(unittest.TestCase):
    def test_dmesg_pipe_capture_rejects_more_than_four_mib_and_reaps_child(self):
        import sys
        popen = USB.subprocess.Popen
        children = []
        def child(*args, **kwargs):
            process = popen([sys.executable, "-c",
                             "import sys,time; sys.stdout.buffer.write(b'x'*5000000); sys.stdout.flush(); time.sleep(10)"], **kwargs)
            children.append(process)
            return process
        with unittest.mock.patch.object(USB.subprocess, "Popen", side_effect=child):
            with self.assertRaisesRegex(ValueError, "exceeds bound"):
                USB.bounded_kernel_history()
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdout.closed)

    def test_dmesg_pipe_capture_returns_normal_bounded_output(self):
        import sys
        popen = USB.subprocess.Popen
        with unittest.mock.patch.object(USB.subprocess, "Popen", side_effect=lambda *args, **kwargs:
             popen([sys.executable, "-c", "print('[1.0] dwc3 diagnostic')"], **kwargs)):
            self.assertEqual(USB.bounded_kernel_history(), b"[1.0] dwc3 diagnostic\n")

    def test_register_host_without_real_xhci_and_root_hub_is_not_ready(self):
        facts = {"xhci_platform_devices": ["bind", "module", "uevent", "unbind"],
                 "usb_devices": [], "udc": ["a600000.usb"]}
        self.assertFalse(USB.host_controller_ready(facts))
        facts["xhci_platform_devices"].append("xhci-hcd.2.auto")
        self.assertFalse(USB.host_controller_ready(facts))
        facts["usb_devices"] = ["usb1", "1-0:1.0"]
        self.assertFalse(USB.host_controller_ready(facts))
        facts["host_controllers"] = [{"controller": "a600000.usb",
                                      "driver": "xhci-hcd", "root_hubs": ["usb1"]}]
        self.assertTrue(USB.host_controller_ready(facts))

    def test_root_hub_must_belong_to_bound_phone_xhci(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            driver = root / "bus/platform/drivers/xhci-hcd"
            driver.mkdir(parents=True)
            device = root / "devices/platform/a600000.usb/xhci-hcd.2.auto"
            device.mkdir(parents=True)
            (device / "driver").symlink_to(driver, target_is_directory=True)
            (driver / device.name).symlink_to(device, target_is_directory=True)
            usb = root / "bus/usb/devices"
            usb.mkdir(parents=True)
            unrelated = root / "devices/platform/unrelated/usb1"
            unrelated.mkdir(parents=True)
            (usb / "usb1").symlink_to(unrelated, target_is_directory=True)
            self.assertFalse(USB.host_controller_ready(USB.inventory(root)))
            hub = device / "usb2"
            hub.mkdir()
            (usb / "usb2").symlink_to(hub, target_is_directory=True)
            facts = USB.inventory(root)
            self.assertTrue(USB.host_controller_ready(facts))
            self.assertEqual(facts["host_controllers"][0]["root_hubs"], ["usb2"])

    def test_usb_kernel_diagnostics_are_bounded_private_and_filtered(self):
        payload = ("unrelated private line\n" + "[1.0] dwc3 failed to initialize host\n" * 100).encode()
        with unittest.mock.patch.object(USB, "bounded_kernel_history", return_value=payload):
            report = USB.kernel_diagnostics()
        self.assertTrue(report["available"])
        self.assertTrue(report["private_only"])
        self.assertEqual(len(report["lines"]), 64)
        self.assertNotIn("unrelated private line", str(report))

    def test_host_reassertion_requires_attached_typec_host_and_source(self):
        import copy
        facts = {"ports": [{"partner_present": True, "data_role": "[host] device",
                            "power_role": "[source] sink"}]}
        self.assertTrue(USB.typec_host_source(facts))
        for field, value in (("partner_present", False), ("data_role", "host [device]"),
                             ("power_role", "source [sink]")):
            changed = copy.deepcopy(facts)
            changed["ports"][0][field] = value
            self.assertFalse(USB.typec_host_source(changed))

    def test_ucsi_disconnect_cannot_force_host_while_still_a_sink(self):
        with tempfile.TemporaryDirectory() as temporary:
            mode = Path(temporary) / "mode"
            mode.write_text("device")
            observations = []
            with unittest.mock.patch.object(USB, "typec_inventory", return_value={
                "ports": [{"partner_present": True, "data_role": "host [device]",
                           "power_role": "source [sink]"}]
            }):
                USB.reconcile_host(mode, observations)
            self.assertEqual(USB.read(mode), "device")
            with unittest.mock.patch.object(USB, "typec_inventory", return_value={
                "ports": [{"partner_present": True, "data_role": "[host] device",
                           "power_role": "[source] sink"}]
            }):
                USB.reconcile_host(mode, observations)
            self.assertEqual(USB.read(mode), "host")
            self.assertTrue(observations[-1]["host_reasserted_for_attached_ufp"])

    def test_read_write_and_inventory_shape(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mode"
            path.write_text("device\n", encoding="utf-8")
            USB.write(path, "host")
            self.assertEqual(USB.read(path), "host")
            rows = USB.inventory()
            self.assertEqual(
                set(rows),
                {
                    "usb_devices",
                    "xhci_platform_devices",
                    "udc",
                    "usb_peripherals",
                    "usb_storage_bound",
                    "usb_hid_bound",
                    "host_controllers",
                },
            )

    def test_external_peripherals_exclude_interfaces_and_omit_serials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            device = root / "bus/usb/devices/1-1"
            interface = root / "bus/usb/devices/1-1:1.0"
            driver = root / "bus/usb/drivers/usb-storage"
            for path in (
                device,
                interface,
                driver,
                root / "bus/platform/drivers/xhci-hcd",
                root / "class/udc",
            ):
                path.mkdir(parents=True)
            (device / "idVendor").write_text("abcd\n", encoding="utf-8")
            (device / "idProduct").write_text("1234\n", encoding="utf-8")
            (device / "product").write_text("Flash drive\n", encoding="utf-8")
            (device / "serial").write_text("private-serial\n", encoding="utf-8")
            (device / "driver").symlink_to(driver, target_is_directory=True)
            rows = USB.inventory(root)
            self.assertEqual([row["path"] for row in rows["usb_peripherals"]], ["1-1"])
            self.assertEqual(rows["usb_peripherals"][0]["product"], "Flash drive")
            self.assertNotIn("serial", rows["usb_peripherals"][0])
            self.assertTrue(rows["usb_storage_bound"])

    def test_platform_device_mode_file_is_discovered(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mode = root / "usb" / "a600000.usb" / "mode"
            mode.parent.mkdir(parents=True)
            mode.write_text("device\n", encoding="utf-8")
            self.assertEqual(USB.mode_paths(root), mode)

    def test_multiple_mode_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for parent in ("usb/a600000.usb", "dwc3.duplicate"):
                mode = root / parent / "mode"
                mode.parent.mkdir(parents=True)
                mode.write_text("device\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "found 2"):
                USB.mode_paths(root)

    def test_host_driver_wait_uses_fresh_inventory(self):
        sequence = [
            {"usb_storage_bound": False, "usb_hid_bound": False},
            {"usb_storage_bound": True, "usb_hid_bound": False},
        ]
        with unittest.mock.patch.object(
            USB, "inventory", side_effect=sequence
        ) as inventory:
            facts, samples = USB.wait_for_host_driver(5, sleep=lambda _: None)
        self.assertTrue(facts["usb_storage_bound"])
        self.assertEqual(samples, 2)
        self.assertEqual(inventory.call_count, 2)

    def test_storage_probe_mounts_read_only_and_unmounts(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.write_bytes(b"usb-read-proof")
            calls = []

            def fake_run(args, **kwargs):
                calls.append(args)
                if args[0] == "mount":
                    Path(args[-1], "proof.txt").write_bytes(source.read_bytes())
                    return Mock(returncode=0, stdout="", stderr="")
                if args[0] == USB.sys.executable:
                    return USB.subprocess.run(args, **kwargs)
                return Mock(returncode=0, stdout="", stderr="")

            result = USB.storage_read_only_probe(
                run=fake_run,
                partitions=lambda: [
                    {"device": "/dev/sda1", "size_sectors": 2048}
                ],
                temporary_root=temporary,
            )
        self.assertTrue(result["read_only_io_pass"])
        self.assertEqual(calls[0][0], "mount")
        self.assertIn("ro,nosuid,nodev,noexec", calls[0])
        self.assertEqual(calls[-1][0], "umount")
        self.assertTrue(result["cleanup_complete"])
        self.assertEqual(result["candidate_limit"], 1)

    def test_storage_probe_only_attempts_one_of_eight_partitions(self):
        calls = []
        def fake_run(args, **kwargs):
            calls.append((args, kwargs["timeout"]))
            return Mock(returncode=1 if args[0] == "mount" else 0,
                        stdout="", stderr="")
        with tempfile.TemporaryDirectory() as temporary:
            result = USB.storage_read_only_probe(
                run=fake_run, temporary_root=temporary,
                partitions=lambda: [{"device": f"/dev/sda{i}", "size_sectors": 8}
                                    for i in range(1, 9)],
            )
        self.assertEqual([call[0][0] for call in calls], ["mount", "umount"])
        self.assertEqual(len(result["attempts"]), 1)
        self.assertFalse(result["read_only_io_pass"])
        self.assertTrue(result["cleanup_complete"])
        self.assertLessEqual(sum(call[1] for call in calls), 32)

    def test_storage_mount_timeout_still_attempts_unmount(self):
        calls = []
        def fake_run(args, **kwargs):
            calls.append(args[0])
            if args[0] == "mount":
                raise USB.subprocess.TimeoutExpired(args, kwargs["timeout"])
            return Mock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as temporary:
            result = USB.storage_read_only_probe(
                run=fake_run, temporary_root=temporary,
                partitions=lambda: [{"device": "/dev/sda1", "size_sectors": 8}],
            )
        self.assertEqual(calls, ["mount", "umount"])
        self.assertTrue(result["attempts"][0]["operation_timed_out"])
        self.assertTrue(result["cleanup_complete"])

    def test_storage_read_timeout_keeps_parent_unmount_budget(self):
        calls = []
        def fake_run(args, **kwargs):
            calls.append((args[0], kwargs["timeout"]))
            if args[0] == USB.sys.executable:
                raise USB.subprocess.TimeoutExpired(args, kwargs["timeout"])
            return Mock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as temporary:
            result = USB.storage_read_only_probe(
                run=fake_run, temporary_root=temporary,
                partitions=lambda: [{"device": "/dev/sda1", "size_sectors": 8}],
            )
        self.assertEqual(calls, [("mount", 12), (USB.sys.executable, 8), ("umount", 12)])
        self.assertFalse(result["read_only_io_pass"])
        self.assertTrue(result["cleanup_complete"])

    def test_storage_unmount_failure_retains_directory_and_fails_cleanup(self):
        def fake_run(args, **kwargs):
            return Mock(returncode=1, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as temporary:
            result = USB.storage_read_only_probe(
                run=fake_run, temporary_root=temporary,
                partitions=lambda: [{"device": "/dev/sda1", "size_sectors": 8}],
            )
            self.assertEqual(len(list(Path(temporary).glob("usb-mount-*"))), 1)
        self.assertFalse(result["cleanup_complete"])
        self.assertFalse(result["read_only_io_pass"])

    def test_large_usb_file_read_is_limited_before_allocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'large'
            with path.open('wb') as stream:
                stream.write(b'x'*4096)
                stream.truncate(1024*1024*1024)
            self.assertEqual(USB.read_regular_prefix(path),b'x'*4096)

    def test_symlink_and_fifo_are_not_proof_files(self):
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            outside=root/'outside'
            outside.write_bytes(b'private')
            mount=root/'mount'
            mount.mkdir()
            link=mount/'link'
            link.symlink_to(outside)
            os.mkfifo(mount/'fifo')
            self.assertEqual(USB.bounded_regular_files(mount),[])
            with self.assertRaises(OSError):
                USB.read_regular_prefix(link)
            with self.assertRaisesRegex(ValueError,'regular file'):
                USB.read_regular_prefix(mount/'fifo')

    def test_directory_inspection_has_entry_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for i in range(40):
                (root/str(i)).mkdir()
            with unittest.mock.patch.object(USB.os,'scandir',wraps=USB.os.scandir) as scan:
                self.assertEqual(USB.bounded_regular_files(root,maximum_entries=10),[])
            self.assertEqual(scan.call_count,1)

    def test_builtin_touchscreen_does_not_qualify_usb_hid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            device=root/'bus/usb/devices/1-1'
            device.mkdir(parents=True)
            builtin=root/'devices/platform/touch/input/event0'
            builtin.mkdir(parents=True)
            events=root/'class/input'
            events.mkdir(parents=True)
            (events/'event0').symlink_to(builtin,target_is_directory=True)
            facts={'usb_peripherals':[{'path':'1-1','drivers':['usbhid']}]}
            self.assertFalse(USB.usb_hid_input_present(facts,root))
            external=device/'1-1:1.0/input/event1'
            external.mkdir(parents=True)
            (events/'event1').symlink_to(external,target_is_directory=True)
            self.assertTrue(USB.usb_hid_input_present(facts,root))

    def test_only_read_only_usb_partitions_are_selected_not_phone_ufs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            blocks=root/'class/block'
            blocks.mkdir(parents=True)
            usb_bus=root/'bus/usb'
            usb_bus.mkdir(parents=True)
            parent=root/'devices/platform/controller/usb1/1-1'
            parent.mkdir(parents=True)
            (parent/'subsystem').symlink_to(usb_bus,target_is_directory=True)
            for name,is_usb,ro in [('sda1',False,'1'),('sdb1',True,'0'),('sdc1',True,'1')]:
                device=(parent if is_usb else root/'devices/platform/ufs')/name
                device.mkdir(parents=True)
                for key,value in {'partition':'1','dev':'8:1','size':'2048','ro':ro}.items():
                    (device/key).write_text(value)
                (blocks/name).symlink_to(device,target_is_directory=True)
            self.assertEqual(USB.storage_partitions(blocks),[{'device':'/dev/sdc1','size_sectors':2048}])

    def test_typec_snapshot_is_read_only_and_omits_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            port=root/'class/typec/port0'
            port.mkdir(parents=True)
            (port/'data_role').write_text('[host] device')
            (port/'power_role').write_text('source [sink]')
            (port/'serial').write_text('private-serial')
            (port.parent/'port0-partner').mkdir()
            adsp=root/'class/remoteproc/remoteproc2'
            adsp.mkdir(parents=True)
            (adsp/'name').write_text('30000000.remoteproc-adsp')
            (adsp/'state').write_text('offline')
            facts=USB.typec_inventory(root)
            self.assertEqual(facts['ports'],[{'port':'port0','data_role':'[host] device','power_role':'source [sink]','partner_present':True}])
            self.assertEqual(facts['adsp'],[{'remoteproc':'remoteproc2','state':'offline'}])
            self.assertNotIn('private-serial',str(facts))
            self.assertTrue(facts['read_only'])


if __name__ == "__main__":
    unittest.main()
