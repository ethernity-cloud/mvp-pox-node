#!/bin/bash
requiredkernelversion="5.13.0-40"
nodefolder=$(pwd)
configfile="config"
rebootfile="/tmp/reboot"
service="etny-vagrant.service"
os=""

if [ "$1" == "-v" ]
then
	ansible_cmd="ansible-playbook -v"
else
	ansible_cmd="ansible-playbook"
fi

ubuntu_20_04(){
#determining if the etny-vagrant service is running. If yes we stop the script as we don't need to run the setup process
echo $os "found. Continuing..."
echo "Finding out if etny-vagrant service is already running"...
systemctl status $service 2>/dev/null | grep "active (running)" > /dev/null
if [ $? -eq 0 ]
then 
	echo "ETNY service already running. Nothing to do. Exiting..."; 
else
	echo "Service not found. Continuing setup..."
	ubuntu_20_04_kernel_check
fi
}

qemu_hold(){

apt-mark hold qemu-system-common
apt-mark hold qemu-system-data
apt-mark hold qemu-system-x86
apt-mark hold qemu-utils

}

qemu_unhold(){

apt-mark unhold qemu-system-common
apt-mark unhold qemu-system-data
apt-mark unhold qemu-system-x86
apt-mark unhold qemu-utils

}

check_config_file(){
        if [ -f $configfile ]
        then
                echo "Config file found. "
        else
                echo "Config file not found. How would you like to continue?"
                ubuntu_20_04_config_file_choice
        fi
}

check_ansible(){
        echo "Check ansible version..."
        ANSIBLE_VERSION=`ansible --version 2> /dev/null || echo ""`
        if [[ $ANSIBLE_VERSION = "" ]]; then echo "Installing latest ansible version..." && sudo apt-add-repository --yes --update ppa:ansible/ansible && sudo apt update && sudo apt -y install software-properties-common ansible; fi
}

is_miminum_kernel_version(){
#returning true or false if we have the minimum required kernel version for Ubuntu 20.04
    version=`uname -r` && currentver=${version%-*} 
    if [ "$(printf '%s\n' "$requiredkernelversion" "$currentver" | sort -V | head -n1)" = "$requiredkernelversion" ]; then echo true ; else echo false; fi
 } 

ubuntu_20_04_kernel_check(){
#if we have the right kernel then we run the ansible-playbook and finish installation
echo "Determining if the right kernel is running..."
if [[ ( "$(is_miminum_kernel_version)" = true && $os = "Ubuntu 20.04" ) || ( $(uname -r) = "5.0.0-050000-generic"  && $os = "Ubuntu 18.04") || ( "$(is_miminum_kernel_version)" = true && $os = "Ubuntu 22.04" )]]
then  
	echo "The right kernel is running. Continuing setup..."
	## check ansible 
	check_ansible
	check_config_file
	echo "Running ansible-playbook script..."	
	HOME=/root
	qemu_unhold
	sudo -E $ansible_cmd -i localhost, playbook.yml -e "ansible_python_interpreter=/usr/bin/python3"
	install_result=$?
	if [ -f $rebootfile ]
	then 
		echo "Restarting system. Please run the installer script afterwards to continue the setup."
		sec=30
		while [ $sec -ge 0 ]; do echo -n "Restarting system in [CTRL+C to cancel]: " && echo -ne "$sec\033[0K\r" && let "sec=sec-1" && sleep 1; done
		sudo reboot
	else
                if [ $install_result == 0 ]
                then
			qemu_hold
			echo "Node installation completed successfully. Please allow up to 24h to see transactions on the blockchain. " && exit
                else
                	echo "Node installation failed! Please check error messages above." && exit
                fi
	fi
else 
	check_config_file
	ubuntu_20_04_update_ansible
fi
}

ubuntu_20_04_config_file_choice(){
#if the config file doesn't exist we offer the either generate one with random wallets or we get the wallets from input
echo "1) Type wallets. "
echo "2) Generate random wallets... "
echo "3) Exit. Rerun the script when config file exists..."
echo -n "[Type your choice to continue]:" && read choice
case "$choice" in 
	1) 
		echo "Type/Paste wallet details below..."
		nodeaddr=("Node Address: " "Node Private Key: " "Result Address: " "Result Private Key: ")
		IFS=""
		for address in ${nodeaddr[@]}; do
			case $address in
				${nodeaddr[0]})
				while true
				do
					echo -n $address && read nodeaddress
					if [[ $nodeaddress = "" ]]; then echo "Node address cannot be empty."; else break; fi
				done;;
				${nodeaddr[2]})
					while true
					do
						echo -n $address && read resultaddress
						if [[ $nodeaddress = $resultaddress ]]
						then 
							echo "Result address must be different than the node address. Try a different address..."
						else break
						fi
					done;;
				${nodeaddr[1]})
					while true
					do
						echo -n $address && read nodeprivatekey
						if [[ ${#nodeprivatekey} = 64 && $nodeprivatekey =~ ^[a-zA-Z0-9]*$ ]]
						then
							break
						else echo "Invalid result private key. Please try again..."
						fi
					done;;
				${nodeaddr[3]})
					while true
					do
						echo -n $address && read resultprivatekey
						if [[ ${#resultprivatekey} = 64 && $resultprivatekey =~ ^[a-zA-Z0-9]*$ ]]
						then
							if [[ $nodeprivatekey = $resultprivatekey ]]
							then
								echo "Result private key must be different than the node private key. Try a different private key..."
							else
								break
							fi
						else echo "Invalid result private key. Please try again..."
						fi
					done;;

			esac
		done
		echo "ADDRESS="$nodeaddress >> $nodefolder/$configfile
		echo "PRIVATE_KEY="$nodeprivatekey >> $nodefolder/$configfile
		echo "RESULT_ADDRESS="$resultaddress >> $nodefolder/$configfile
		echo "RESULT_PRIVATE_KEY="$resultprivatekey >> $nodefolder/$configfile
		if [ -f $nodefolder/$configfile ]; then echo "Config file generated successfully. Continuing..." && ubuntu_20_04_kernel_check; else echo "Something went wrong. Seek Help!" && exit; fi
	;;
	2) 
		export FILE=generate
		check_ansible
		ubuntu_20_04_ansible_playbook;;
	3) echo "Exiting..." && exit;;
	*) echo "Invalid choice. Please choose an option below..." && ubuntu_20_04_config_file_choice;;
esac
}

ubuntu_20_04_ansible_playbook(){
#running the ansible-playbook command and restart system automatically
echo "Running ansible-playbook..."
cd && cd $nodefolder
HOME=/root
sudo -E $ansible_cmd -i localhost, playbook.yml -e "ansible_python_interpreter=/usr/bin/python3"
install_result=$?
if [ -f $rebootfile ]
then 
	echo "Restarting system. Please run the installer script afterwards to continue the setup."
	sec=30
	while [ $sec -ge 0 ]; do echo -n "Restarting system in [CTRL+C to cancel]: " && echo -ne "$sec\033[0K\r" && let "sec=sec-1" && sleep 1; done
	sudo reboot
else
        if [ $install_result == 0 ]
        then
               echo "Node installation completed successfully. Please allow up to 24h to see transactions on the blockchain. " && exit
        else
               echo "Node installation failed! Please check error messages above." && exit
        fi
fi
}


ubuntu_20_04_update_ansible(){
#If we don't have the right kernel running that means we didn't update the system
echo "We don't have the right kernel running."
echo "Updating system, kernel and installing ansible..."
sudo sudo apt-add-repository --yes --update ppa:ansible/ansible && sudo apt update && sudo apt upgrade -y && sudo apt autoremove -y &&  sudo apt -y install software-properties-common ansible
if [ $? -eq 0 ]
then 
	echo "Update successfull. Continuing..."
	ubuntu_20_04_ansible_playbook	
fi
}

ubuntu(){
#Getting which version of Ubuntu is instaled
echo "Ubuntu OS found. Determining version..."
case $(awk '/^VERSION_ID=/' /etc/*-release 2>/dev/null | awk -F'=' '{ print tolower($2) }' | tr -d '"') in
	20.04) 
		os='Ubuntu 20.04'
		ubuntu_20_04;;
	18.04) 
		os='Ubuntu 18.04'
		ubuntu_20_04;;
	22.04) 
		os='Ubuntu 22.04'
		ubuntu_20_04;;
	*) echo "Version not supported. Exiting..."
esac
}

start(){
#getting which Linux distribution is installed
echo "Getting distro..."
case $(awk '/^ID=/' /etc/*-release 2>/dev/null | awk -F'=' '{ print tolower($2) }' | tr -d '"') in
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
