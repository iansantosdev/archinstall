import os
from pathlib import Path
from typing import Any, TYPE_CHECKING

import archinstall
from archinstall import info, debug
from archinstall import SysInfo
from archinstall.lib import disk
from archinstall.lib.global_menu import GlobalMenu
from archinstall.default_profiles.applications.pipewire import PipewireProfile
from archinstall.lib.configuration import ConfigurationOutput
from archinstall.lib.installer import Installer
from archinstall.lib.menu import Menu
from archinstall.lib.mirrors import use_mirrors
from archinstall.lib.models.bootloader import Bootloader
from archinstall.lib.models.network_configuration import NetworkConfigurationHandler
from archinstall.lib.networking import check_mirror_reachable
from archinstall.lib.profile.profiles_handler import profile_handler

if TYPE_CHECKING:
	_: Any


if archinstall.arguments.get('help'):
	print("See `man archinstall` for help.")
	exit(0)


def ask_user_questions():
	"""
		First, we'll ask the user for a bunch of user input.
		Not until we're satisfied with what we want to install
		will we continue with the actual installation steps.
	"""

	# ref: https://github.com/archlinux/archinstall/pull/831
	# we'll set NTP to true by default since this is also
	# the default value specified in the menu options; in
	# case it will be changed by the user we'll also update
	# the system immediately
	global_menu = GlobalMenu(data_store=archinstall.arguments)

	global_menu.enable('archinstall-language')

	global_menu.enable('keyboard-layout')

	# Set which region to download packages from during the installation
	global_menu.enable('mirror-region')

	global_menu.enable('sys-language')

	global_menu.enable('sys-encoding')

	global_menu.enable('disk_config', mandatory=True)

	# Specify disk encryption options
	global_menu.enable('disk_encryption')

	# Ask which boot-loader to use (will only ask if we're in UEFI mode, otherwise will default to GRUB)
	global_menu.enable('bootloader')

	global_menu.enable('swap')

	# Get the hostname for the machine
	global_menu.enable('hostname')

	# Ask for a root password (optional, but triggers requirement for super-user if skipped)
	global_menu.enable('!root-password', mandatory=True)

	global_menu.enable('!users', mandatory=True)

	# Ask for archinstall-specific profiles_bck (such as desktop environments etc)
	global_menu.enable('profile_config')

	# Ask about audio server selection if one is not already set
	global_menu.enable('audio')

	# Ask for preferred kernel:
	global_menu.enable('kernels')

	global_menu.enable('packages')

	if archinstall.arguments.get('advanced', False):
		# Enable parallel downloads
		global_menu.enable('parallel downloads')

	# Ask or Call the helper function that asks the user to optionally configure a network.
	global_menu.enable('nic')

	global_menu.enable('timezone')

	global_menu.enable('ntp')

	global_menu.enable('additional-repositories')

	global_menu.enable('__separator__')

	global_menu.enable('save_config')
	global_menu.enable('install')
	global_menu.enable('abort')

	global_menu.run()


def perform_installation(mountpoint: Path):
	"""
	Performs the installation steps on a block device.
	Only requirement is that the block devices are
	formatted and setup prior to entering this function.
	"""
	info('Starting installation')
	disk_config: disk.DiskLayoutConfiguration = archinstall.arguments['disk_config']

	# Retrieve list of additional repositories and set boolean values appropriately
	enable_testing = 'testing' in archinstall.arguments.get('additional-repositories', [])
	enable_multilib = 'multilib' in archinstall.arguments.get('additional-repositories', [])

	locale = f"{archinstall.arguments.get('sys-language', 'en_US')} {archinstall.arguments.get('sys-encoding', 'UTF-8').upper()}"

	disk_encryption: disk.DiskEncryption = archinstall.arguments.get('disk_encryption', None)

	with Installer(
		mountpoint,
		disk_config,
		disk_encryption=disk_encryption,
		kernels=archinstall.arguments.get('kernels', ['linux'])
	) as installation:
		# Mount all the drives to the desired mountpoint
		if disk_config.config_type != disk.DiskLayoutType.Pre_mount:
			installation.mount_ordered_layout()

		installation.sanity_check()

		if disk_config.config_type != disk.DiskLayoutType.Pre_mount:
			if disk_encryption and disk_encryption.encryption_type != disk.EncryptionType.NoEncryption:
				# generate encryption key files for the mounted luks devices
				installation.generate_key_files()

		# Set mirrors used by pacstrap (outside of installation)
		if archinstall.arguments.get('mirror-region', None):
			use_mirrors(archinstall.arguments['mirror-region'])  # Set the mirrors for the live medium

		installation.minimal_installation(
			testing=enable_testing,
			multilib=enable_multilib,
			hostname=archinstall.arguments.get('hostname', 'archlinux'),
			locales=[locale]
		)

		if archinstall.arguments.get('mirror-region') is not None:
			if archinstall.arguments.get("mirrors", None) is not None:
				installation.set_mirrors(archinstall.arguments['mirror-region'])  # Set the mirrors in the installation medium

		if archinstall.arguments.get('swap'):
			installation.setup_swap('zram')

		if archinstall.arguments.get("bootloader") == Bootloader.Grub and SysInfo.has_uefi():
			installation.add_additional_packages("grub")

		installation.add_bootloader(archinstall.arguments["bootloader"])

		# If user selected to copy the current ISO network configuration
		# Perform a copy of the config
		network_config = archinstall.arguments.get('nic', None)

		if network_config:
			handler = NetworkConfigurationHandler(network_config)
			handler.config_installer(
				installation,
				archinstall.arguments.get('profile_config', None)
			)

		if archinstall.arguments.get('packages', None) and archinstall.arguments.get('packages', None)[0] != '':
			installation.add_additional_packages(archinstall.arguments.get('packages', None))

		if users := archinstall.arguments.get('!users', None):
			installation.create_users(users)

		if audio := archinstall.arguments.get('audio', None):
			info(f'Installing audio server: {audio}')
			if audio == 'pipewire':
				PipewireProfile().install(installation)
			elif audio == 'pulseaudio':
				installation.add_additional_packages("pulseaudio")
		else:
			info("No audio server will be installed")

		if profile_config := archinstall.arguments.get('profile_config', None):
			profile_handler.install_profile_config(installation, profile_config)

		if timezone := archinstall.arguments.get('timezone', None):
			installation.set_timezone(timezone)

		if archinstall.arguments.get('ntp', False):
			installation.activate_time_syncronization()

		if archinstall.accessibility_tools_in_use():
			installation.enable_espeakup()

		if (root_pw := archinstall.arguments.get('!root-password', None)) and len(root_pw):
			installation.user_set_pw('root', root_pw)

		# This step must be after profile installs to allow profiles_bck to install language pre-requisits.
		# After which, this step will set the language both for console and x11 if x11 was installed for instance.
		installation.set_keyboard_language(archinstall.arguments['keyboard-layout'])

		if profile_config := archinstall.arguments.get('profile_config', None):
			profile_config.profile.post_install(installation)

		# If the user provided a list of services to be enabled, pass the list to the enable_service function.
		# Note that while it's called enable_service, it can actually take a list of services and iterate it.
		if archinstall.arguments.get('services', None):
			installation.enable_service(archinstall.arguments.get('services', []))

		# If the user provided custom commands to be run post-installation, execute them now.
		if archinstall.arguments.get('custom-commands', None):
			archinstall.run_custom_user_commands(archinstall.arguments['custom-commands'], installation)

		installation.genfstab()

		info("For post-installation tips, see https://wiki.archlinux.org/index.php/Installation_guide#Post-installation")

		if not archinstall.arguments.get('silent'):
			prompt = str(_('Would you like to chroot into the newly created installation and perform post-installation configuration?'))
			choice = Menu(prompt, Menu.yes_no(), default_option=Menu.yes()).run()
			if choice.value == Menu.yes():
				try:
					installation.drop_to_shell()
				except:
					pass

	debug(f"Disk states after installing: {disk.disk_layouts()}")


if archinstall.arguments.get('skip-mirror-check', False) is False and check_mirror_reachable() is False:
	log_file = os.path.join(archinstall.storage.get('LOG_PATH', None), archinstall.storage.get('LOG_FILE', None))
	info(f"Arch Linux mirrors are not reachable. Please check your internet connection and the log file '{log_file}'.")
	exit(1)

if not archinstall.arguments.get('silent'):
	ask_user_questions()

config_output = ConfigurationOutput(archinstall.arguments)

if not archinstall.arguments.get('silent'):
	config_output.show()

config_output.save()

if archinstall.arguments.get('dry_run'):
	exit(0)

if not archinstall.arguments.get('silent'):
	input(str(_('Press Enter to continue.')))

fs_handler = disk.FilesystemHandler(
	archinstall.arguments['disk_config'],
	archinstall.arguments.get('disk_encryption', None)
)

fs_handler.perform_filesystem_operations()

perform_installation(archinstall.storage.get('MOUNT_POINT', Path('/mnt')))
