"""CPU arithmetic, native allocation release and asynchronous monitor lifecycle."""
from __future__ import annotations

import ctypes as C
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication
from leap_desktop.resource_monitor import (ResourceMonitor, _MacReader, _VMStatistics64,
                                           _SMCKeyData, _SLOW_FIELDS, _cpu_percent, _memory_bytes)


class ResourceArithmeticTests(unittest.TestCase):
    def test_first_sample_and_zero_tick_delta_are_unavailable(self):
        ticks = [(10, 20, 70, 0)]
        self.assertEqual(_cpu_percent(None, ticks), (None, [None]))
        self.assertEqual(_cpu_percent(ticks, ticks), (None, [None]))
        self.assertEqual(_cpu_percent(ticks, None), (None, []))
        self.assertEqual(_cpu_percent(ticks, ticks * 2), (None, [None, None]))

    def test_cpu_uses_each_core_and_total_tick_weight(self):
        total, cores = _cpu_percent([(0, 0, 0, 0)] * 2, [(10, 5, 80, 5), (15, 10, 25, 0)])
        self.assertEqual(cores, [20.0, 50.0])
        self.assertEqual(total, 30.0)

    def test_cpu_natural_tick_wrap_is_unsigned(self):
        total, cores = _cpu_percent([(0xFFFFFFFE, 0, 20, 0)], [(2, 0, 26, 0)])
        self.assertEqual((total, cores), (40.0, [40.0]))

    def test_memory_uses_physical_compressor_pages_and_excludes_file_cache(self):
        vm = _VMStatistics64()
        vm.internal_page_count = 500
        vm.purgeable_count = 80
        vm.wire_count = 100
        vm.compressor_page_count = 60
        vm.external_page_count = 1000
        vm.total_uncompressed_pages_in_compressor = 20000
        self.assertEqual(_memory_bytes(vm, 16384), {
            "memory_used_bytes": 580 * 16384, "memory_compressed_bytes": 60 * 16384})

    def test_mach_cpu_allocation_is_freed_even_for_invalid_shape(self):
        reader = _MacReader.__new__(_MacReader)
        reader._host, reader._task = 1, 2
        reader._deallocate = Mock()
        ticks = (C.c_uint32 * 4)(10, 10, 80, 0)

        def fill(host, flavor, cores, address, count):
            C.cast(cores, C.POINTER(C.c_uint32))[0] = 1
            C.cast(address, C.POINTER(C.c_void_p))[0] = C.addressof(ticks)
            C.cast(count, C.POINTER(C.c_uint32))[0] = 4
            return 0

        reader._processor_info = fill
        self.assertEqual(reader.cpu_ticks(), [(10, 10, 80, 0)])
        reader._deallocate.assert_called_once_with(2, C.addressof(ticks), 16)
        reader._deallocate.reset_mock()

        def malformed(*args):
            fill(*args)
            C.cast(args[-1], C.POINTER(C.c_uint32))[0] = 3
            return 0

        reader._processor_info = malformed
        self.assertIsNone(reader.cpu_ticks())
        reader._deallocate.assert_called_once_with(2, C.addressof(ticks), 12)

    def test_gpu_dictionary_is_released_when_field_is_unavailable(self):
        reader = _MacReader.__new__(_MacReader)
        reader._gpu = 42
        reader._keys = {"PerformanceStatistics": 100, "Device Utilization %": 101}
        reader._property = Mock(return_value=200)
        reader._release = Mock()
        reader._type_id = Mock(return_value=4)
        reader._dictionary_type = 4
        reader._dictionary_get = Mock(return_value=None)
        self.assertIsNone(reader._gpu_percent())
        reader._release.assert_called_once_with(200)

    def test_close_releases_owned_handles_once(self):
        reader = _MacReader.__new__(_MacReader)
        reader._gpu, reader._host, reader._task, reader._smc = 42, 23, 5, 51
        reader._temperature_keys = {"cpu": (123,), "gpu": ()}
        reader._keys = {"one": 1, "two": 2}
        reader._io_release, reader._release, reader._port_deallocate = Mock(), Mock(), Mock()
        reader._service_close = Mock()
        reader.close()
        reader.close()
        reader._io_release.assert_called_once_with(42)
        self.assertEqual(reader._release.call_count, 2)
        reader._port_deallocate.assert_called_once_with(5, 23)
        reader._service_close.assert_called_once_with(51)
        self.assertEqual(reader._keys, {})
        self.assertIsNone(reader._temperature_keys)

    def test_cpu_types_follow_logical_ids_despite_physical_gaps_or_enumeration_order(self):
        reader = _MacReader.__new__(_MacReader)
        reader._keys = {"logical-cpu-id": 1, "cluster-type": 2}
        reader._entry_from_path = Mock(return_value=10)
        reader._io_release, reader._release = Mock(), Mock()

        def iterator(root, plane, address):
            C.cast(address, C.POINTER(C.c_uint32))[0] = 20
            return 0

        reader._child_iterator = iterator
        reader._iterator_next = Mock(side_effect=[101, 102, 103, 104, 105, 106, 0])
        properties = {(101, 1): 201, (101, 2): 301, (102, 1): 202, (102, 2): 302,
                      (103, 1): 203, (103, 2): 303, (104, 1): 204,
                      (105, 1): 205, (105, 2): 305, (106, 1): 206, (106, 2): 306}
        reader._property = Mock(side_effect=lambda child, key, *_: properties.get((child, key)))
        # A physical cpu10 can have logical ID 9; iterator order is not CPU order.
        reader._number = Mock(side_effect=lambda value: {201: 9., 202: 0., 203: 3., 204: -1., 205: 0., 206: 1.}[value])
        reader._data_type = 7
        reader._type_id = Mock(return_value=7)
        buffers = {301: C.create_string_buffer(b"P"), 302: C.create_string_buffer(b"E"),
                   303: C.create_string_buffer(b"?"), 305: C.create_string_buffer(b"P"),
                   306: C.create_string_buffer(b"E")}
        reader._data_length = Mock(return_value=2)
        reader._data_bytes = Mock(side_effect=lambda value: C.addressof(buffers[value]))
        reader._cpu_types = reader._read_cpu_types()
        expected = [None] * 14
        expected[1] = "E"
        expected[9] = "P"
        self.assertEqual(reader.cpu_core_types(14), expected)  # Duplicate ID 0 is ambiguous.
        self.assertEqual(reader._cpu_types, {9: "P", 0: None, 3: None, 1: "E"})
        self.assertEqual({call.args[0] for call in reader._release.call_args_list}, set(properties.values()))
        self.assertEqual([call.args[0] for call in reader._io_release.call_args_list], [101, 102, 103, 104, 105, 106, 20, 10])

    def test_gpu_group_count_uses_mgpu_field_and_releases_dictionary(self):
        reader = _MacReader.__new__(_MacReader)
        reader._gpu = 42
        reader._keys = {"GPUConfigurationVariable": 100, "num_mgpus": 101}
        reader._property = Mock(return_value=200)
        reader._release = Mock()
        reader._type_id, reader._dictionary_type = Mock(return_value=4), 4
        reader._dictionary_get = Mock(return_value=300)
        reader._number = Mock(side_effect=[4., 3.5, 0., None])
        self.assertEqual(reader._read_gpu_groups(), 4)
        for _ in range(3):
            self.assertIsNone(reader._read_gpu_groups())
        self.assertEqual(reader._release.call_count, 4)
        self.assertTrue(all(call.args == (200, 101) for call in reader._dictionary_get.call_args_list))


class TemperatureReaderTests(unittest.TestCase):
    def setUp(self):
        self.reader = _MacReader.__new__(_MacReader)
        self.reader._smc = 0
        self.reader._task = 1
        self.reader._temperature_keys = None
        self.reader._read_sysctl_text = Mock(return_value="Apple M3 Max")
        self.reader._matching = Mock(return_value=11)
        self.reader._matching_service = Mock(return_value=12)
        self.reader._io_release = Mock()

        def open_service(service, task, kind, address):
            C.cast(address, C.POINTER(C.c_uint32))[0] = 13
            return 0

        self.reader._service_open = Mock(side_effect=open_service)
        self.requests = []

        def native_read(connection, selector, request_address, request_size, response_address, response_size):
            request = C.cast(request_address, C.POINTER(_SMCKeyData)).contents
            response = C.cast(response_address, C.POINTER(_SMCKeyData)).contents
            self.requests.append((request.key, request.command))
            self.assertEqual((connection, selector, request_size), (13, 2, 80))
            self.assertIn(request.command, (5, 9))
            if request.command == 9:
                response.key_info.size = 4
                response.key_info.type = int.from_bytes(b"flt " if request.key != 0x42616431 else b"ui32", "big")
            else:
                self.assertEqual(request.key_info.size, 4)
                response.bytes[:4] = bytes(C.c_float(60 if request.key == 0x54663034 else 80))
            return 0

        self.reader._smc_call = Mock(side_effect=native_read)

    def test_smc_layout_and_no_write_command(self):
        self.assertEqual(C.sizeof(_SMCKeyData), 80)
        self.assertEqual((_SMCKeyData.key_info.offset, _SMCKeyData.result.offset,
                          _SMCKeyData.command.offset, _SMCKeyData.bytes.offset), (28, 40, 42, 48))
        self.reader._smc = 13
        self.assertIsNone(self.reader._smc_read(0, 6))
        self.reader._smc_call.assert_not_called()

    def test_only_identified_chip_family_opens_smc(self):
        for name in (None, "Apple M2 Max", "Apple M4", "Intel Core", "Apple M3 Unknown"):
            self.reader._read_sysctl_text.return_value = name
            self.reader._open_temperature_sensors()
        self.reader._matching.assert_not_called()
        self.assertTrue(all(value is None for value in self.reader._temperatures().values()))

    def test_denied_smc_connection_releases_service_without_reading_sensors(self):
        self.reader._service_open.side_effect = None
        self.reader._service_open.return_value = 0xE00002E2
        self.reader._open_temperature_sensors()
        self.reader._io_release.assert_called_once_with(12)
        self.reader._smc_call.assert_not_called()
        self.assertTrue(all(value is None for value in self.reader._temperatures().values()))

    def test_metadata_is_cached_and_wrong_types_are_never_read_as_temperature(self):
        with patch("leap_desktop.resource_monitor._M3_TEMPERATURE_KEYS",
                   {"cpu": ("Tf04", "Bad1"), "gpu": ("Tf14",)}):
            self.reader._open_temperature_sensors()
        self.reader._io_release.assert_called_once_with(12)
        self.assertEqual([command for _, command in self.requests], [9, 9, 9])
        for _ in range(2):
            self.assertEqual(self.reader._temperatures(), {
                "cpu_temperature_c": 60., "cpu_temperature_sensor_count": 1,
                "gpu_temperature_c": 80., "gpu_temperature_sensor_count": 1})
        self.assertEqual([command for _, command in self.requests], [9, 9, 9, 5, 5, 5, 5])
        self.assertNotIn((0x42616431, 5), self.requests)

    def test_failed_or_nonfinite_samples_are_excluded_from_sensor_average(self):
        self.reader._temperature_keys = {"cpu": tuple(range(7)), "gpu": (7,)}

        def response(key, command):
            if key in (2, 7):
                return None
            result = _SMCKeyData()
            result.bytes[:4] = bytes(C.c_float({0: 50, 1: 70, 3: float("nan"),
                                              4: float("inf"), 5: 0, 6: -1}[key]))
            return result

        self.reader._smc_read = Mock(side_effect=response)
        self.assertEqual(self.reader._temperatures(), {
            "cpu_temperature_c": 60., "cpu_temperature_sensor_count": 2,
            "gpu_temperature_c": None, "gpu_temperature_sensor_count": 0})

    def test_native_error_firmware_error_and_short_message_are_unavailable(self):
        self.reader._smc = 13
        self.reader._smc_call = Mock(return_value=1)
        self.assertIsNone(self.reader._smc_read(0, 5))

        def firmware_error(*args):
            C.cast(args[-2], C.POINTER(_SMCKeyData)).contents.result = 0x84
            return 0

        self.reader._smc_call.side_effect = firmware_error
        self.assertIsNone(self.reader._smc_read(0, 5))

        def short_message(*args):
            C.cast(args[-1], C.POINTER(C.c_size_t))[0] = 48
            return 0

        self.reader._smc_call.side_effect = short_message
        self.assertIsNone(self.reader._smc_read(0, 5))


class FakeReader:
    def __init__(self):
        self.cpu_calls = 0
        self.slow_calls = 0
        self.closed = False
        self.threads = []

    def cpu_ticks(self):
        self.threads.append(threading.get_ident())
        self.cpu_calls += 1
        return [(20 * self.cpu_calls, 0, 80 * self.cpu_calls, 0)]

    def cpu_core_types(self, count):
        return ["E"] * count

    def slow_sample(self):
        self.slow_calls += 1
        return dict.fromkeys(_SLOW_FIELDS)

    def close(self):
        self.closed = True
        self.threads.append(threading.get_ident())


class ResourceMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.reader = FakeReader()
        factory_patch = patch("leap_desktop.resource_monitor._system_reader", return_value=self.reader)
        self.factory = factory_patch.start()
        self.addCleanup(factory_patch.stop)
        self.monitor = ResourceMonitor()
        self.snapshots = []
        self.delivery_threads = []
        self.monitor.snapshotReady.connect(self.received)

    def received(self, snapshot):
        self.snapshots.append(snapshot)
        self.delivery_threads.append(QThread.currentThread())

    def wait_until(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.005)
        self.fail("Resource monitor did not reach the required boundary")

    def tearDown(self):
        self.monitor.shutdown()
        self.wait_until(self.monitor.isDrained)
        self.assertFalse(self.monitor._thread.isRunning())

    def test_inactive_construction_and_shutdown_do_not_read_or_start_thread(self):
        self.app.processEvents()
        self.factory.assert_not_called()
        self.assertFalse(self.monitor._thread.isRunning())
        self.assertTrue(self.monitor.shutdown())
        self.monitor.setActive(True)
        self.factory.assert_not_called()

    def test_sampling_cadence_pause_resume_and_gui_delivery(self):
        self.monitor.setActive(True)
        self.wait_until(lambda: len(self.snapshots) >= 3)
        self.assertIsNone(self.snapshots[0]["cpu_percent"])
        self.assertEqual(self.snapshots[0]["cpu_cores"], [None])
        self.assertEqual(self.snapshots[0]["cpu_core_types"], ["E"])
        self.assertEqual(self.snapshots[1]["cpu_percent"], 20)
        self.assertEqual(self.reader.slow_calls, 2)
        self.assertTrue(all(thread == self.app.thread() for thread in self.delivery_threads))
        self.assertTrue(all(thread != threading.get_ident() for thread in self.reader.threads))
        self.monitor.setActive(False)
        calls, slow_calls, samples = self.reader.cpu_calls, self.reader.slow_calls, len(self.snapshots)
        time.sleep(1.15)
        self.app.processEvents()
        self.assertEqual((self.reader.cpu_calls, self.reader.slow_calls, len(self.snapshots)), (calls, slow_calls, samples))
        self.monitor.setActive(True)
        self.wait_until(lambda: len(self.snapshots) > samples)
        self.assertIsNone(self.snapshots[-1]["cpu_percent"])
        self.assertEqual(self.snapshots[-1]["cpu_cores"], [None])

    def test_busy_gui_retains_only_latest_snapshot_and_pause_discards_it(self):
        self.monitor.setActive(True)
        time.sleep(2.15)
        self.assertGreaterEqual(self.reader.cpu_calls, 3)
        self.assertEqual(self.snapshots, [])
        self.assertTrue(self.monitor._thread._delivery_pending)
        self.app.processEvents()
        self.assertEqual(len(self.snapshots), 1)
        self.assertEqual(self.snapshots[0]["cpu_percent"], 20)
        self.monitor.setActive(False)
        self.app.processEvents()
        self.assertEqual(len(self.snapshots), 1)

    def test_inflight_read_does_not_block_pause_or_shutdown_and_is_discarded(self):
        entered, release = threading.Event(), threading.Event()
        original = self.reader.cpu_ticks

        def held_read():
            entered.set()
            release.wait(3)
            return original()

        self.reader.cpu_ticks = held_read
        drained = []
        self.monitor.drained.connect(lambda: drained.append(True))
        self.monitor.setActive(True)
        self.wait_until(entered.is_set)
        started = time.monotonic()
        self.monitor.setActive(False)
        self.assertFalse(self.monitor.shutdown())
        self.assertLess(time.monotonic() - started, .1)
        self.assertEqual(drained, [])
        release.set()
        self.wait_until(lambda: bool(drained))
        self.assertTrue(self.reader.closed)
        self.assertEqual(self.reader.slow_calls, 0)
        self.assertEqual(self.snapshots, [])
        self.assertTrue(self.monitor.shutdown())
        self.assertEqual(drained, [True])

    def test_inflight_slow_sample_cannot_publish_after_pause(self):
        entered, release = threading.Event(), threading.Event()
        original = self.reader.slow_sample

        def held_slow_read():
            entered.set()
            release.wait(3)
            return original()

        self.reader.slow_sample = held_slow_read
        self.monitor.setActive(True)
        self.wait_until(entered.is_set)
        self.monitor.setActive(False)
        release.set()
        self.monitor.shutdown()
        self.wait_until(self.monitor.isDrained)
        self.assertEqual(self.snapshots, [])
        self.assertTrue(self.reader.closed)


if __name__ == "__main__":
    unittest.main()
