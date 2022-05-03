Intel(R) Software Guard Extensions Software Enabling Application for Linux\*
===========================================================================

Introduction
------------

This application will enable Intel SGX on Linux systems where the BIOS supports
Intel SGX, but does not provide an explicit option to enable it. These
systems can only enable Intel SGX via the "software enable" procedure.

License
-------

This application is distributed under the BSD 3-Clause "New" or "Revised"
License.

Requirements
------------

Performing the software enable procedure requires:

 * An Intel SGX capable processor
 * A BIOS that supports Intel SGX and the software enable procedure. Some BIOS manufacturers provide an option to explicitly enable or disable Intel SGX.
 * A [supported Linux distribution](https://github.com/intel/linux-sgx) that has been booted in UEFI mode. Systems booted in Legacy mode cannot perform the software enable as the procedure depends on EFI variables. **A Legacy mode system can be enabled by booting a Linux Live CD in UEFI mode, and then performing the software enable. Intel SGX enabling occurs at the platform, not OS, level.**
 * The EFI filesystem, which should be mounted by default if your Linux system is booted in UEFI mode.

Building the Application
------------------------

Build the application by running 'make'.

```
$ make
```

There are no package requirements beyond a C compiler. If you wish to use a
compiler other than gcc*, edit the Makefile and change the ```CC``` variable.

There is no installer, as this is a one-time use executable. Once Intel SGX is
enabled, it will stay enabled until explicitly disabled in your BIOS (if your
BIOS supports this capability).

Running the Application
-----------------------

Usage is:

```
usage: sgx-enable [ options ]

Options:
  -s, --status      Report the enabling status only
  -h, --help        Show this help
```

Running *sgx_enable* with no options will attempt to perform the software
enabling procedure on your system. You will need write access to the EFI
filesystem which typically means it must be run as root:

```
$ sudo ./sgx_enable
```

*Once the software enabling procedure has completed successfully you will need
to reboot your system for Intel SGX to be available.*

The software enabling procedure is a one-time procedure. You will not need to
run this application again unless you explicitly disable Intel SGX in your
BIOS at a later date.

The **--status** option prints the status of Intel SGX on your system and
does not attempt to enable it. This will also report whether or not your
system supports Intel SGX and the software enable procedure.

```
$ sgx_enable --status
```

You should not need to be *root* to display the enabling status of the system
unless your EFI filesystem is not world-readable.

The utility will report the enabling status on your system, and the success
or failure of the software enabling procedure.

Result Messages
---------------

> **Intel SGX is disabled and can be enabled using this utility**

Your system supports Intel SGX, and is in the "software enable" state. Rerun
the utility without the `--status` option to enable Intel SGX.

> **Intel SGX is already enabled on this system**

Your system supports Intel SGX and it has already been enabled. No further
action is necessary.

> **Software enable has been set. Please reboot your system.**

The software enabling procedure completed successfully. Once your system is
rebooted Intel SGX will be available for use.

> **You may need to rerun this utility as root**

The software enabling procedure could not be performed because you do not
have write access to the EFI filesystem. Rerun the utility as *root*.

> **This CPU does not support Intel SGX**

Your CPU does not support Intel SGX.

> **Intel SGX is explicitly disabled on your system**

Either Intel SGX is explicitly disabled in your BIOS, or your BIOS does not
support Intel SGX. Reboot your system into the BIOS setup screen and
look for an Intel SGX option. If you don't find any, your system may not
support Intel SGX.

Contact your OEM for assistance.

> **This processor supports Intel SGX but was booted in legacy mode**

A UEFI booted system is required to perform the software enabling procedure.
If your system has already been built and booted in Legacy mode, you can
boot a Linux Live CD in UEFI mode and perform the procedure from the Live
image.

> **Intel SGX is explicitly disabled, and your BIOS does not support the "software enable" option**

Your BIOS provides explicit options to enable or disable Intel SGX, and does
not have a software enable capability. To enable Intel SGX, boot your system
into the BIOS setup screen, locate the Intel SGX options and explicitly
set the status to "enabled". You do not need this utility.

Contact your OEM for assistance.

> **The software enable has been performed on this system and Intel SGX will be enabled after the system is rebooted**

The software enable procedure has already been executed. You need to reboot
your system for Intel SGX to be enabled for use. You do not need to run this
utility again.

> **I could not attempt the software enable**

Your system supports Intel SGX and is in the software enable state, but the
software enabling could not be completed because an unexpected error occurred.
This is almost always the result of a system error. See the accompanying
error message for clues as to what went wrong.

> **I couldn't make sense of your system**

Something terrible has happened. This message usually means that an unexpected
error has occurred and is almost always the result of a more serious
system error. Look at the error message output for clues as to what might
have gone wrong.
