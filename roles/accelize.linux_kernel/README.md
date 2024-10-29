[![Build Status](https://dev.azure.com/Accelize/DRM/_apis/build/status/Ansible%20-%20Linux%20Kernel?branchName=master)](https://dev.azure.com/Accelize/DRM/_build/latest?definitionId=26&branchName=master)

Linux Kernel Role
=================

This Ansible role install and enable a specific kernel version from OS repositories and to ensure matching kernel headers are installed.

Requirements
------------

The role requires to be run as root on the target host.

The specified Kernel version must be supported by OS repositories (This include "Vault" repositories for Red hat based distributions).

Role Variables
--------------

* **install_kernel_headers**: If True, also install matching kernel headers.
  Default to `true`.
* **kernel_version**: Install the most recent kernel version available that start by this value (Keep the current kernel version if matching).
  Default to any version.
* **reboot_on_kernel_update**: If True, reboot the system if the kernel was updated.
  Default to `true`.
* **kernel_variant**: If specified on a Debian based distributions, use the required kernel variant (like "", "common", "generic", "aws", "azure", ...) else use the current kernel variant.

Example Playbook
----------------

```yaml
- hosts: servers
  become: true  
  roles:
     - role: accelize.linux_kernel
  vars:
     kernel_version: 3.10.0-693
```

Dependencies
------------

None.

License
-------

Apache 2.0

Author Information
------------------

This role is provided by [Accelize](https://www.accelize.com).
