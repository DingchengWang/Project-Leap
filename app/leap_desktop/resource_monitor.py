"""Read-only, whole-machine macOS resource samples for the running-task panel.

CPU is a one-second delta of Mach per-logical-CPU ticks, normalized to 0–100.
GPU, temperatures, physical memory and swap refresh every two seconds. Memory used means
resident anonymous pages minus purgeable pages, plus wired pages and physical
compressor pages; file cache and the compressor's uncompressed logical size are
excluded. All memory fields are bytes, timestamp is Unix seconds, and unavailable
values are None. GPU utilization is the driver's device-wide percentage; there
is no per-GPU-core estimate or privileged helper. GPU groups are the driver's
mGPU groups. Temperatures are arithmetic means of valid, identified CPU/GPU
sensors, not averages over enabled cores. Only the verified M3 sensor family is
used; unavailable or unrecognized hardware stays unavailable.
"""
from __future__ import annotations

import ctypes as C
import math
import sys
import threading
import time

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot


_SLOW_FIELDS = ("gpu_percent", "gpu_core_count", "gpu_group_count",
                "cpu_temperature_c", "gpu_temperature_c",
                "cpu_temperature_sensor_count", "gpu_temperature_sensor_count",
                "memory_used_bytes", "memory_total_bytes", "memory_compressed_bytes", "swap_used_bytes")

# Identified M3 temperature groups, including sensors on harvested-core models.
# https://github.com/exelban/stats/blob/master/Modules/Sensors/values.swift
_M3_TEMPERATURE_KEYS = {
    "cpu": ("Te05", "Te0L", "Te0P", "Te0S", "Tf04", "Tf09", "Tf0A", "Tf0B",
            "Tf0D", "Tf0E", "Tf44", "Tf49", "Tf4A", "Tf4B", "Tf4D", "Tf4E"),
    "gpu": ("Tf14", "Tf18", "Tf19", "Tf1A", "Tf24", "Tf28", "Tf29", "Tf2A"),
}


class _SMCKeyInfo(C.Structure):
    _fields_ = [("size", C.c_uint32), ("type", C.c_uint32), ("attributes", C.c_uint8)]


class _SMCKeyData(C.Structure):
    # AppleSMC's 80-byte user-client message. The version area includes padding.
    # Read protocol: https://github.com/exelban/stats/blob/master/SMC/smc.swift
    _fields_ = [("key", C.c_uint32), ("version", C.c_uint8 * 8),
                ("power_limits", C.c_uint32 * 4), ("key_info", _SMCKeyInfo),
                ("result", C.c_uint8), ("status", C.c_uint8), ("command", C.c_uint8),
                ("data32", C.c_uint32), ("bytes", C.c_uint8 * 32)]


class _VMStatistics64(C.Structure):
    # Public mach/vm_statistics.h; natural_t remains 32-bit on arm64.
    _fields_ = ([(name, C.c_uint32) for name in
                 ("free_count", "active_count", "inactive_count", "wire_count")]
                + [(name, C.c_uint64) for name in
                   ("zero_fill_count", "reactivations", "pageins", "pageouts", "faults",
                    "cow_faults", "lookups", "hits", "purges")]
                + [("purgeable_count", C.c_uint32), ("speculative_count", C.c_uint32)]
                + [(name, C.c_uint64) for name in
                   ("decompressions", "compressions", "swapins", "swapouts")]
                + [(name, C.c_uint32) for name in
                   ("compressor_page_count", "throttled_count", "external_page_count", "internal_page_count")]
                + [("total_uncompressed_pages_in_compressor", C.c_uint64), ("swapped_count", C.c_uint64)])


class _SwapUsage(C.Structure):
    _fields_ = [("total", C.c_uint64), ("available", C.c_uint64), ("used", C.c_uint64),
                ("page_size", C.c_uint32), ("encrypted", C.c_int32)]


def _bind(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = list(arguments)
    return function


class _MacReader:
    """Own all Mach/IOKit/CF handles in the sampling thread; never spawn a process."""

    def __init__(self):
        self._system = C.CDLL("/usr/lib/libSystem.B.dylib")
        self._io = C.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        self._cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        u32, ptr, size = C.c_uint32, C.c_void_p, C.c_size_t
        self._host_self = _bind(self._system, "mach_host_self", u32)
        self._processor_info = _bind(self._system, "host_processor_info", C.c_int, u32, C.c_int,
                                    C.POINTER(u32), C.POINTER(ptr), C.POINTER(u32))
        self._deallocate = _bind(self._system, "vm_deallocate", C.c_int, u32, size, size)
        self._port_deallocate = _bind(self._system, "mach_port_deallocate", C.c_int, u32, u32)
        self._host_statistics = _bind(self._system, "host_statistics64", C.c_int, u32, C.c_int,
                                     ptr, C.POINTER(u32))
        self._host_page_size = _bind(self._system, "host_page_size", C.c_int, u32, C.POINTER(u32))
        self._sysctl = _bind(self._system, "sysctlbyname", C.c_int, C.c_char_p, ptr,
                            C.POINTER(size), ptr, size)
        self._matching = _bind(self._io, "IOServiceMatching", ptr, C.c_char_p)
        self._matching_service = _bind(self._io, "IOServiceGetMatchingService", u32, u32, ptr)
        self._property = _bind(self._io, "IORegistryEntryCreateCFProperty", ptr, u32, ptr, ptr, u32)
        self._entry_from_path = _bind(self._io, "IORegistryEntryFromPath", u32, u32, C.c_char_p)
        self._child_iterator = _bind(self._io, "IORegistryEntryGetChildIterator", C.c_int,
                                     u32, C.c_char_p, C.POINTER(u32))
        self._iterator_next = _bind(self._io, "IOIteratorNext", u32, u32)
        self._io_release = _bind(self._io, "IOObjectRelease", C.c_int, u32)
        self._service_open = _bind(self._io, "IOServiceOpen", C.c_int, u32, u32, u32, C.POINTER(u32))
        self._service_close = _bind(self._io, "IOServiceClose", C.c_int, u32)
        self._smc_call = _bind(self._io, "IOConnectCallStructMethod", C.c_int,
                               u32, u32, ptr, size, ptr, C.POINTER(size))
        self._string = _bind(self._cf, "CFStringCreateWithCString", ptr, ptr, C.c_char_p, u32)
        self._release = _bind(self._cf, "CFRelease", None, ptr)
        self._type_id = _bind(self._cf, "CFGetTypeID", C.c_ulong, ptr)
        self._number_type = _bind(self._cf, "CFNumberGetTypeID", C.c_ulong)()
        self._dictionary_type = _bind(self._cf, "CFDictionaryGetTypeID", C.c_ulong)()
        self._data_type = _bind(self._cf, "CFDataGetTypeID", C.c_ulong)()
        self._number_get = _bind(self._cf, "CFNumberGetValue", C.c_bool, ptr, C.c_int, ptr)
        self._dictionary_get = _bind(self._cf, "CFDictionaryGetValue", ptr, ptr, ptr)
        self._data_length = _bind(self._cf, "CFDataGetLength", C.c_long, ptr)
        self._data_bytes = _bind(self._cf, "CFDataGetBytePtr", ptr, ptr)
        self._task = u32.in_dll(self._system, "mach_task_self_").value
        self._host = self._host_self()
        self._gpu = 0
        self._smc = 0
        self._temperature_keys = None
        self._keys = {}
        try:
            for name in ("PerformanceStatistics", "Device Utilization %", "gpu-core-count",
                         "GPUConfigurationVariable", "num_mgpus", "logical-cpu-id", "cluster-type"):
                key = self._string(None, name.encode("utf-8"), 0x08000100)
                if not key:
                    raise OSError("Could not allocate a resource-statistic key")
                self._keys[name] = key
            matching = self._matching(b"IOAccelerator")
            # IOServiceGetMatchingService consumes the matching dictionary.
            if matching:
                self._gpu = self._matching_service(0, matching)
            page_size = u32()
            self._page_size = (page_size.value if self._host_page_size(self._host, C.byref(page_size)) == 0
                               else None)
            total = C.c_uint64()
            self._total = total.value if self._read_sysctl(b"hw.memsize", total) else None
            self._gpu_core_count = self._read_gpu_cores()
            self._gpu_group_count = self._read_gpu_groups()
            self._cpu_types = self._read_cpu_types()
            self._open_temperature_sensors()
        except (OSError, ValueError):
            self.close()
            raise

    def _read_sysctl(self, name, value):
        length = C.c_size_t(C.sizeof(value))
        return self._sysctl(name, C.byref(value), C.byref(length), None, 0) == 0 and length.value == C.sizeof(value)

    def _read_sysctl_text(self, name):
        value = C.create_string_buffer(128)
        length = C.c_size_t(C.sizeof(value))
        if self._sysctl(name, value, C.byref(length), None, 0) != 0:
            return None
        if not 1 < length.value <= C.sizeof(value) or value.raw[length.value - 1] != 0:
            return None
        return value.raw[:length.value - 1].decode("utf-8", errors="replace")

    def _read_cpu_types(self):
        # XNU publishes logical-cpu-id using ml_get_cpu_number. host_processor_info
        # iterates processor_array by that same logical ID; node names/cpu-id can
        # have gaps on harvested-core models and must not be used as array indices.
        root = self._entry_from_path(0, b"IODeviceTree:/cpus")
        if not root:
            return {}
        iterator = C.c_uint32()
        types = {}
        try:
            if self._child_iterator(root, b"IODeviceTree", C.byref(iterator)) != 0:
                return types
            while child := self._iterator_next(iterator.value):
                try:
                    logical = self._property(child, self._keys["logical-cpu-id"], None, 0)
                    if not logical:
                        continue
                    try:
                        index = self._number(logical)
                    finally:
                        self._release(logical)
                    if index is None or index < 0 or not index.is_integer():
                        continue
                    index = int(index)
                    kind = None
                    value = self._property(child, self._keys["cluster-type"], None, 0)
                    if value:
                        try:
                            if self._type_id(value) == self._data_type:
                                length = self._data_length(value)
                                if length in (1, 2):
                                    raw = C.string_at(self._data_bytes(value), length).rstrip(b"\0")
                                    if raw in (b"E", b"P"):
                                        kind = raw.decode("ascii")
                        finally:
                            self._release(value)
                    types[index] = None if index in types else kind
                finally:
                    self._io_release(child)
        finally:
            if iterator.value:
                self._io_release(iterator.value)
            self._io_release(root)
        return types

    def cpu_core_types(self, count):
        return [self._cpu_types.get(index) for index in range(count)]

    def cpu_ticks(self):
        count, cores, address = C.c_uint32(), C.c_uint32(), C.c_void_p()
        status = self._processor_info(self._host, 2, C.byref(cores), C.byref(address), C.byref(count))
        if status != 0:
            return None
        try:
            if not address.value or not cores.value or count.value != cores.value * 4:
                return None
            ticks = C.cast(address, C.POINTER(C.c_uint32))
            return [tuple(ticks[index * 4 + state] for state in range(4)) for index in range(cores.value)]
        finally:
            if address.value:
                self._deallocate(self._task, address.value, count.value * C.sizeof(C.c_uint32))

    def _number(self, value):
        if not value or self._type_id(value) != self._number_type:
            return None
        number = C.c_double()
        if not self._number_get(value, 13, C.byref(number)) or not math.isfinite(number.value):
            return None
        return number.value

    def _read_gpu_cores(self):
        if not self._gpu:
            return None
        value = self._property(self._gpu, self._keys["gpu-core-count"], None, 0)
        if not value:
            return None
        try:
            number = self._number(value)
            return int(number) if number is not None and number > 0 and number.is_integer() else None
        finally:
            self._release(value)

    def _read_gpu_groups(self):
        if not self._gpu:
            return None
        config = self._property(self._gpu, self._keys["GPUConfigurationVariable"], None, 0)
        if not config:
            return None
        try:
            if self._type_id(config) != self._dictionary_type:
                return None
            number = self._number(self._dictionary_get(config, self._keys["num_mgpus"]))
            return int(number) if number is not None and number > 0 and number.is_integer() else None
        finally:
            self._release(config)

    def _open_temperature_sensors(self):
        if self._read_sysctl_text(b"machdep.cpu.brand_string") not in ("Apple M3", "Apple M3 Pro", "Apple M3 Max"):
            return
        matching = self._matching(b"AppleSMC")
        service = self._matching_service(0, matching) if matching else 0
        if not service:
            return
        connection = C.c_uint32()
        try:
            if self._service_open(service, self._task, 0, C.byref(connection)) != 0:
                return
            self._smc = connection.value
        finally:
            self._io_release(service)
        if not self._smc:
            return
        self._temperature_keys = {}
        for group, names in _M3_TEMPERATURE_KEYS.items():
            keys = []
            for name in names:
                key = int.from_bytes(name.encode("ascii"), "big")
                info = self._smc_read(key, 9)
                if info is not None and info.key_info.size == 4 and info.key_info.type == int.from_bytes(b"flt ", "big"):
                    keys.append(key)
            self._temperature_keys[group] = tuple(keys)

    def _smc_read(self, key, command):
        # The only permitted requests are key metadata (9) and sensor reads (5).
        if not self._smc or command not in (5, 9):
            return None
        request, response = _SMCKeyData(), _SMCKeyData()
        request.key, request.command = key, command
        if command == 5:
            request.key_info.size = 4  # Only verified float32 sensors are cached.
        size = C.c_size_t(C.sizeof(response))
        status = self._smc_call(self._smc, 2, C.byref(request), C.sizeof(request), C.byref(response), C.byref(size))
        if status != 0 or size.value != C.sizeof(response) or response.result != 0:
            return None
        return response

    def _temperatures(self):
        result = {f"{group}_temperature_{suffix}": None
                  for group in ("cpu", "gpu") for suffix in ("c", "sensor_count")}
        if self._temperature_keys is None:
            return result
        for group, keys in self._temperature_keys.items():
            values = []
            for key in keys:
                response = self._smc_read(key, 5)
                if response is None:
                    continue
                value = C.c_float.from_buffer_copy(bytes(response.bytes[:4])).value
                # SMC uses an all-zero value for unavailable temperature sensors.
                if math.isfinite(value) and value > 0:
                    values.append(value)
            result[f"{group}_temperature_sensor_count"] = len(values)
            result[f"{group}_temperature_c"] = sum(values) / len(values) if values else None
        return result

    def _gpu_percent(self):
        if not self._gpu:
            return None
        stats = self._property(self._gpu, self._keys["PerformanceStatistics"], None, 0)
        if not stats:
            return None
        try:
            if self._type_id(stats) != self._dictionary_type:
                return None
            value = self._number(self._dictionary_get(stats, self._keys["Device Utilization %"]))
            return value if value is not None and 0 <= value <= 100 else None
        finally:
            self._release(stats)

    def slow_sample(self):
        result = dict.fromkeys(_SLOW_FIELDS)
        result.update(gpu_percent=self._gpu_percent(), gpu_core_count=self._gpu_core_count,
                      gpu_group_count=self._gpu_group_count, memory_total_bytes=self._total)
        result.update(self._temperatures())
        vm = _VMStatistics64()
        count = C.c_uint32(C.sizeof(vm) // C.sizeof(C.c_int))
        status = self._host_statistics(self._host, 4, C.byref(vm), C.byref(count))
        # rev1 includes all fields used below; do not interpret a shorter response.
        required = (_VMStatistics64.internal_page_count.offset + C.sizeof(C.c_uint32)) // C.sizeof(C.c_int)
        if status == 0 and count.value >= required and self._page_size:
            result.update(_memory_bytes(vm, self._page_size))
        swap = _SwapUsage()
        if self._read_sysctl(b"vm.swapusage", swap):
            result["swap_used_bytes"] = swap.used
        return result

    def close(self):
        if self._smc:
            self._service_close(self._smc)
            self._smc = 0
        self._temperature_keys = None
        if self._gpu:
            self._io_release(self._gpu)
            self._gpu = 0
        for value in self._keys.values():
            self._release(value)
        self._keys.clear()
        if self._host:
            self._port_deallocate(self._task, self._host)
            self._host = 0


def _memory_bytes(vm, page_size):
    return {"memory_used_bytes": (max(0, vm.internal_page_count - vm.purgeable_count)
                                  + vm.wire_count + vm.compressor_page_count) * page_size,
            "memory_compressed_bytes": vm.compressor_page_count * page_size}


def _cpu_percent(previous, current):
    if current is None:
        return None, []
    if previous is None or len(previous) != len(current):
        return None, [None] * len(current)
    per_core, total_busy, total_ticks = [], 0, 0
    for before, after in zip(previous, current):
        delta = [(new - old) & 0xFFFFFFFF for old, new in zip(before, after)]
        total = sum(delta)
        busy = total - delta[2]  # CPU_STATE_IDLE; user/system/nice count as busy.
        per_core.append(100.0 * busy / total if total else None)
        total_busy += busy
        total_ticks += total
    return (100.0 * total_busy / total_ticks if total_ticks else None), per_core


class _UnavailableReader:
    def cpu_ticks(self):
        return None

    def cpu_core_types(self, count):
        return [None] * count

    def slow_sample(self):
        return dict.fromkeys(_SLOW_FIELDS)

    def close(self):
        pass


def _system_reader():
    if sys.platform != "darwin":
        return _UnavailableReader()
    try:
        return _MacReader()
    except (OSError, AttributeError):
        return _UnavailableReader()


class _SamplingThread(QThread):
    available = Signal()

    def __init__(self, reader_factory, parent):
        super().__init__(parent)
        self.setObjectName("LeapResourceMonitor")
        self._reader_factory = reader_factory
        self._condition = threading.Condition()
        self._active = False
        self._stopping = False
        self._generation = 0
        self._latest = None
        self._delivery_pending = False

    def activate(self, active, generation):
        with self._condition:
            self._active, self._generation = active, generation
            self._latest = None
            self._condition.notify_all()

    def stop(self):
        with self._condition:
            self._stopping = True
            self._latest = None
            self._condition.notify_all()

    def take_snapshot(self):
        with self._condition:
            result, self._latest = self._latest, None
            self._delivery_pending = False
            return result

    def run(self):
        reader = None
        previous = None
        generation = -1
        next_cpu = next_slow = 0.0
        slow = dict.fromkeys(_SLOW_FIELDS)
        try:
            while True:
                with self._condition:
                    while not self._stopping:
                        if not self._active:
                            self._condition.wait()
                            continue
                        if generation != self._generation:
                            generation = self._generation
                            previous = None
                            next_cpu = next_slow = 0.0
                            slow = dict.fromkeys(_SLOW_FIELDS)
                        delay = next_cpu - time.monotonic()
                        if delay > 0:
                            self._condition.wait(delay)
                            continue
                        break
                    if self._stopping:
                        return
                if reader is None:
                    reader = self._reader_factory()
                with self._condition:
                    if self._stopping or not self._active or generation != self._generation:
                        continue
                now = time.monotonic()
                current = reader.cpu_ticks()
                total, cores = _cpu_percent(previous, current)
                previous = current
                with self._condition:
                    if self._stopping or not self._active or generation != self._generation:
                        continue
                if now >= next_slow:
                    slow = reader.slow_sample()
                    next_slow = now + 2.0
                snapshot = {"timestamp": time.time(), "cpu_percent": total, "cpu_cores": cores,
                            "cpu_core_types": reader.cpu_core_types(len(cores)), **slow}
                next_cpu = now + 1.0
                with self._condition:
                    if self._stopping or not self._active or generation != self._generation:
                        continue
                    self._latest = generation, snapshot
                    notify = not self._delivery_pending
                    self._delivery_pending = True
                if notify:
                    self.available.emit()
        finally:
            if reader is not None:
                reader.close()


class ResourceMonitor(QObject):
    """GUI-thread owner. Call shutdown and retain it until drained before deletion.

    Creating the monitor does not start a thread or read resources. Pausing blocks
    delivery immediately; an already-entered read can finish but is discarded.
    Reactivation starts a fresh CPU baseline. Exactly one pending snapshot is kept
    if the GUI is busy. shutdown is terminal and nonblocking; it returns True when
    already drained, otherwise emits drained after native resources are released.
    """
    snapshotReady = Signal(dict)
    drained = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._generation = 0
        self._closing = False
        self._started = False
        self._drained = True
        self._thread = _SamplingThread(_system_reader, self)
        self._thread.available.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self._thread.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)

    @Slot(bool)
    def setActive(self, active):
        active = bool(active)
        if self._closing or active == self._active:
            return
        self._active = active
        self._generation += 1
        self._thread.activate(active, self._generation)
        if active and not self._started:
            self._started = True
            self._drained = False
            self._thread.start()

    @Slot()
    def _deliver(self):
        packet = self._thread.take_snapshot()
        if packet is not None and self._active and not self._closing and packet[0] == self._generation:
            self.snapshotReady.emit(packet[1])

    @Slot()
    def _finished(self):
        self._active = False
        self._drained = True
        self.drained.emit()

    def isDrained(self):
        return self._drained

    @Slot(result=bool)
    def shutdown(self):
        self._closing = True
        self._active = False
        self._thread.stop()
        return self._drained
