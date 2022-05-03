#!/bin/bash
#if [ $(uname -r) = "5.13.0-40-generic" ]; then echo "SUCCESS"; else echo "FAIL"; fi

ubuntu_20_04(){
echo "Ubuntu 20.04 found. Continuing..."
echo "Find out if service is already running"...
systemctl status etny-vagrant.service | grep "active (running)" > /dev/null
if [ $? -eq 0 ]
then 
	echo "ETNY service already running. Nothing to do. Exiting..."; 
else
	ubuntu_20_04_kernel_check
fi
}

ubuntu_20_04_kernel_check(){
echo "Determining if the right kernel is running"
if [ $(uname -r) = "5.13.0-40-generic" ]
then  
	echo "The right kernel is running"
	echo "Continuing setup"
	echo "Verifying if the repository has been cloned"
	cd 
	if [ -d "etny-node" ]
	then
		echo "Reposity already cloned. Running ansible..."
		cd && cd etny-node	
		sudo ansible-playbook -i localhost, playbook.yml -e "ansible_python_interpreter=/usr/bin/python3"	
	else
		clone_repository
	fi
else 
	ubuntu_20_04_update_ansible
fi
}

clone_repository(){
echo "Cloning the repository"
cd
#git clone https://github.com/ethernity-cloud/mvp-pox-node.git
git clone --branch Ubuntu20-Adrian https://gitlab.ethernity.cloud/iosif/etny-node.git
ubuntu_20_04_kernel_check
}


ubuntu_20_04_update_ansible(){
echo "We don't have the right kernel running"
echo "Updating system and installing ansible"
sudo apt update && sudo apt upgrade -y && sudo apt autoremove -y && sudo apt-add-repository --yes --update ppa:ansible/ansible && sudo apt -y install software-properties-common ansible && sudo ansible-galaxy install uoi-io.libvirt
if [ $? -eq 0 ]
then 
	echo "Update successfull. Continuing..."
	cd && cd etny-node
	sudo ansible-playbook -i localhost, playbook.yml -e "ansible_python_interpreter=/usr/bin/python3"
fi
}

ubuntu(){
echo "Ubuntu OS found. Determining version..."
case $(awk '/^VERSION_ID=/' /etc/*-release | awk -F'=' '{ print tolower($2) }' | tr -d '"') in
	20.04) ubuntu_20_04;;
	18.04) echo "Ubuntu 18.04 installation is not supported by this script yet. Please follow the step by step guide at https://docs.ethernity.cloud/ethernity-node/installing-the-node for installation instructions.";;
	22.04) echo "22.04";;
	*) echo "Version not supported. Exiting..."
esac
}

start(){
echo "Getting distro..."
case $(awk '/^ID=/' /etc/*-release | awk -F'=' '{ print tolower($2) }' | tr -d '"') in
	ubuntu) ubuntu;;
#	debian) echo "debian distro Found. Not Supported. Exiting...";;
#	centos) echo "centos distro Found. Not Supported. Exiting...";;
#	manjaro) echo "manjaro distro Found. Not Supported. Exiting...";;
#	arch) echo "arch distro Found. Not Supported. Exiting...";;
#	rhel) echo "red hat  distro Found. Not Supported. Exiting...";;
#	fedora) echo "fedora distro Found. Not Supported. Exiting...";;
	*) echo "Could not determine Distro. Exiting..."
esac
}
start
