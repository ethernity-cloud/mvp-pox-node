# Ethernity NODE

This repository provides requirements and setup instructions to enable Ethernity NODE capabilities on a computing system.

## Hardware requirements:

### CPU

Lists with SGX feature enabled are maintained by Intel at: 

[SGX with Intel® ME](https://ark.intel.com/content/www/us/en/ark/search/featurefilter.html?productType=873&2_SoftwareGuardExtensions=Yes%20with%20Intel%C2%AE%20ME)
[SGX with Intel® SPS and Intel® ME](https://ark.intel.com/content/www/us/en/ark/search/featurefilter.html?productType=873&2_SoftwareGuardExtensions=Yes%20with%20both%20)

### BIOS

SGX must be set to ENABLED by the system owner via BIOS.

### Compatible Systems

List of compatible systems:

A list of SGX compatible systems that support Intel SGX is maintained here:
<https://github.com/ayeks/SGX-hardware>

### Tested systems

We have tested successfully the following hardware:
DELL Optiplex 5040

## Software requirements:

Currently the following operating systems are suported:

```
Ubuntu 18.04
Ubuntu 20.04
Ubuntu 22.04

```

We are planning support for the following operating systems:

```
Debian 10*
Fedora 30*
CentOS 7*
RHEL 8*

```

## Installation

### Automated Installation
Please check the automated process at https://github.com/ethernity-cloud/etny-node-installer.

For Manual installation please continue reading below.

### 1. Install ansible

```bash
$ sudo apt update
$ sudo apt -y install software-properties-common
$ sudo apt-add-repository --yes --update ppa:ansible/ansible
$ sudo apt -y install ansible
```


### 2. Clone the repository

```
$ git clone https://github.com/ethernity-cloud/mvp-pox-node.git
```


### 3. Install the kernel with SGX support

```bash
$ cd mvp-pox-node
$ sudo ansible-playbook -i localhost, playbook.yml \
  -e "ansible_python_interpreter=/usr/bin/python3"
```

After the first run of the script, the new kernel(with SGX support) is installed and the following message will be displayed:

```
ok: [localhost] => {
    "msg": "The kernel has been updated, a reboot is required"
}
```

Reboot the system as requested.


### 4. Create config file (please use your own wallets):

```bash
$ cd mvp-pox-node
$ cat << EOF > config
ADDRESS=0xf17f52151EbEF6C7334FAD080c5704D77216b732
PRIVATE_KEY=AE6AE8E5CCBFB04590405997EE2D52D2B330726137B875053C36D94E974D162F
RESULT_ADDRESS=0xC5fdf4076b8F3A5357c5E395ab970B5B54098Fef
RESULT_PRIVATE_KEY=0DBBE8E4AE425A6D2687F1A7E3BA17BC98C673636790F1B8AD91193C05875EF1
EOF
$
```


### 5. Start the node

```bash
$ cd mvp-pox-node
$ sudo ansible-playbook -i localhost, playbook.yml \
  -e "ansible_python_interpreter=/usr/bin/python3"
```

After the second run of the script the node should be successfully installed and the following message will be seen on the screen:

```
ok: [localhost] => {
    "msg": "Ethernity NODE installation successful"
}
```

### 6. Check if the service is running correctly.

Service status can be seen by running the below command.

```
systemctl status etny-vagrant.service
```
### 7. For Ubuntu 18.04 and 20.04 installations if you'd like to upgrade the virtual machine from Ubuntu 18.04 to 22.04

Please run the commands below
```
$ cd && cd mvp-pox-node
$ git pull 
$ sudo ansible-playbook -i localhost, playbook.yml \
  -e "ansible_python_interpreter=/usr/bin/python3"
```

# ETNY Node Installer

This installer provides an easy way to automate the installation process of an Ethernity Node as much as possible.

Features:
-	Automates the system update, kernel update (5.0.0-050000-generic for ubuntu 18.04 and 5.13.0-41-generic for ubuntu 20.04) and runs the ansible-playbook installation process
-	Asks the user to generate (using the “ethkey” tool) or to input wallet details from console (node and result)
-	Checks wallet balance for Bergs (continues only if Bergs > 0)
-	Validates wallet for wrong input 
-	Prevents the user to continue if the node wallet  is the same as the result wallet
-	Restarts the system automatically after the system and kernel is updated

## Usage Instructions

### 1. Clone the repository to the home folder and run it
```
$ cd && git clone https://github.com/ethernity-cloud/mvp-pox-node.git
$ cd mvp-pox-node
$ ./etny-node-installer.sh
```

### 2. Run the script again after system restart
```
$ cd mvp-pox-node
$ ./etny-node-installer.sh