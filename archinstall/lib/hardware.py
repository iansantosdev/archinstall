import os
from functools import cached_property
from pathlib import Path
from typing import Optional, Dict

from .general import SysCommand
from .networking import list_interfaces, enrich_iface_types
from .exceptions import SysCallError
from .output import debug

AVAILABLE_GFX_DRIVERS = {
	# Sub-dicts are layer-2 options to be selected
	# and lists are a list of packages to be installed
	"All open-source (default)": [
		"mesa",
		"xf86-video-amdgpu",
		"xf86-video-ati",
		"xf86-video-nouveau",
		"xf86-video-vmware",
		"libva-mesa-driver",
		"libva-intel-driver",
		"intel-media-driver",
		"vulkan-radeon",
		"vulkan-intel",
	],
	"AMD / ATI (open-source)": [
		"mesa",
		"xf86-video-amdgpu",
		"xf86-video-ati",
		"libva-mesa-driver",
		"vulkan-radeon",
	],
	"Intel (open-source)": [
		"mesa",
		"libva-intel-driver",
		"intel-media-driver",
		"vulkan-intel",
	],
	"Nvidia (open kernel module for newer GPUs, Turing+)": ["nvidia-open"],
	"Nvidia (open-source nouveau driver)": [
		"mesa",
		"xf86-video-nouveau",
		"libva-mesa-driver"
	],
	"Nvidia (proprietary)": ["nvidia"],
	"VMware / VirtualBox (open-source)": ["mesa", "xf86-video-vmware"],
}


class _SysInfo:
	def __init__(self):
		pass

	@cached_property
	def cpu_info(self) -> Dict[str, str]:
		"""
		Returns system cpu information
		"""
		cpu_info_path = Path("/proc/cpuinfo")
		cpu: Dict[str, str] = {}

		with cpu_info_path.open() as file:
			for line in file:
				if line := line.strip():
					key, value = line.split(":", maxsplit=1)
					cpu[key.strip()] = value.strip()

		return cpu

	@cached_property
	def mem_info(self) -> Dict[str, int]:
		"""
		Returns system memory information
		"""
		mem_info_path = Path("/proc/meminfo")
		mem_info: Dict[str, int] = {}

		with mem_info_path.open() as file:
			for line in file:
				key, value = line.strip().split(':')
				num = value.split()[0]
				mem_info[key] = int(num)

		return mem_info

	def mem_info_by_key(self, key: str) -> int:
		return self.mem_info[key]


_sys_info = _SysInfo()


class SysInfo:
	@staticmethod
	def has_wifi() -> bool:
		ifaces = list(list_interfaces().values())
		return 'WIRELESS' in enrich_iface_types(ifaces).values()

	@staticmethod
	def has_uefi() -> bool:
		return os.path.isdir('/sys/firmware/efi')

	@staticmethod
	def _graphics_devices() -> Dict[str, str]:
		cards: Dict[str, str] = {}
		for line in SysCommand("lspci"):
			if b' VGA ' in line or b' 3D ' in line:
				_, identifier = line.split(b': ', 1)
				cards[identifier.strip().decode('UTF-8')] = str(line)
		return cards

	@staticmethod
	def has_nvidia_graphics() -> bool:
		return any('nvidia' in x.lower() for x in SysInfo._graphics_devices())

	@staticmethod
	def has_amd_graphics() -> bool:
		return any('amd' in x.lower() for x in SysInfo._graphics_devices())

	@staticmethod
	def has_intel_graphics() -> bool:
		return any('intel' in x.lower() for x in SysInfo._graphics_devices())

	@staticmethod
	def cpu_vendor() -> Optional[str]:
		return _sys_info.cpu_info.get('vendor_id', None)

	@staticmethod
	def cpu_model() -> Optional[str]:
		return _sys_info.cpu_info.get('model name', None)

	@staticmethod
	def sys_vendor() -> str:
		with open(f"/sys/devices/virtual/dmi/id/sys_vendor") as vendor:
			return vendor.read().strip()

	@staticmethod
	def product_name() -> str:
		with open(f"/sys/devices/virtual/dmi/id/product_name") as product:
			return product.read().strip()

	@staticmethod
	def mem_available() -> int:
		return _sys_info.mem_info_by_key('MemAvailable')

	@staticmethod
	def mem_free() -> int:
		return _sys_info.mem_info_by_key('MemFree')

	@staticmethod
	def mem_total() -> int:
		return _sys_info.mem_info_by_key('MemTotal')

	@staticmethod
	def virtualization() -> Optional[str]:
		try:
			return str(SysCommand("systemd-detect-virt")).strip('\r\n')
		except SysCallError as err:
			debug(f"Could not detect virtual system: {err}")

		return None

	@staticmethod
	def is_vm() -> bool:
		try:
			result = SysCommand("systemd-detect-virt")
			return b"none" not in b"".join(result).lower()
		except SysCallError as err:
			debug(f"System is not running in a VM: {err}")

		return False
