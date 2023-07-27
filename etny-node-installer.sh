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

function print_network_menu {
    echo "#############################################"
    echo "Please select the network:"
    echo "1. Open Beta"
    echo "2. Testnet"
    echo "3. Quit"
    echo "#############################################"
}

while true; do
    print_network_menu
    read -p "Enter your choice: " choice
    case $choice in
        1)
            echo "You selected Open Beta."
            # Extract CONTRACT_ADDRESS from existing .env.config
            CONTRACT_ADDRESS=$(grep "^CONTRACT_ADDRESS" $nodefolder/node/.env | cut -d '=' -f2)
            # Write CONTRACT_ADDRESS to new .env.config
            echo "NETWORK_CONTRACT_ADDRESS=${CONTRACT_ADDRESS}" > $nodefolder/.env.config
            echo "Set NETWORK_CONTRACT_ADDRESS in $nodefolder/.env.config"
            break
            ;;
        2)
            echo "You selected Testnet."
            # Extract TESTNET_CONTRACT_ADDRESS from existing .env.config
            TESTNET_CONTRACT_ADDRESS=$(grep "^TESTNET_CONTRACT_ADDRESS" $nodefolder/node/.env | cut -d '=' -f2)
            # Write TESTNET_CONTRACT_ADDRESS to new .env.config
            echo "NETWORK_CONTRACT_ADDRESS=${TESTNET_CONTRACT_ADDRESS}" > $nodefolder/.env.config
            echo "Set NETWORK_CONTRACT_ADDRESS to TESTNET_CONTRACT_ADDRESS in $nodefolder/.env.config"
            break
            ;;
        3)
            echo "Quitting..."
            exit 0
            ;;
        *)
            echo "Invalid choice. Please select a valid option."
            ;;
    esac
done

task_price_check() {
    if grep -q "TASK_EXECUTION_PRICE" "$nodefolder/$configfile"; then
        current_price=$(grep "TASK_EXECUTION_PRICE" "$nodefolder/$configfile" | cut -d'=' -f2)
        echo "Task execution price already exists in the config file and is currently set to $current_price ETNY/hour."
        echo "Would you like to modify it? (y/N)"
        read modify
	if [[ "$modify" =~ ^[Yy]$ ]]; then
        set_task_price
    	fi
    else
        echo "The TASK_EXECUTION_PRICE is not set in the config file."
        echo "Do you want to use the default value of 3 ETNY/hour? (Y/n)"
        read -r use_default
        if [[ -z "$use_default" ]] || [[ "$use_default" =~ ^[Yy]$ ]]; then
            default_price=3
            echo "TASK_EXECUTION_PRICE=$default_price" >> "$nodefolder/$configfile"
            echo "Task execution price set to default value $default_price ETNY/hour."
        else
            set_task_price
        fi
    fi
}

set_task_price() {
    while true; do
        echo -n "Enter the Task Execution Price (Recommended price for executing a task/hour: 1 - 10 ETNY): "
        read taskprice
        if [[ $taskprice =~ ^[1-9]$|^10$ ]]; then
            break
        else
            echo "Invalid task execution price. Please enter a valid integer price within the recommended range (1 - 10 ETNY)..."
        fi
    done
    sed -i "/TASK_EXECUTION_PRICE/d" "$nodefolder/$configfile"
    echo "TASK_EXECUTION_PRICE=$taskprice" >> "$nodefolder/$configfile"
}

ubuntu_20_04() {
  # Determining if the etny-vagrant service is running
  echo "$os found. Continuing..."
  echo "Finding out if etny-vagrant service is already running..."
  systemctl status "$service" 2>/dev/null | grep "active (running)" >/dev/null
  if [ $? -eq 0 ]; then
    echo "The service is currently running."
    read -p "Would you like to stop the service? (Y/n) " choice
    choice="${choice:-Y}"  # Set the default value to "Y" if the input is empty
    if [[ "$choice" =~ ^[Yy]$ ]]; then
      echo "Stopping the service..."
      # Stop the service here
      systemctl stop "$service"
      ubuntu_20_04_kernel_check
    else
      echo "The service is currently running. Setup aborted."
      exit 1
    fi
  else
    echo "The service is not running."
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

check_config_file() {
    if [ -f "$configfile" ]; then
        echo "Config file found."
        missing_informations=()
        if ! grep -q "^ADDRESS=0x[[:xdigit:]]\{40\}$" "$configfile"; then
            missing_informations+=("ADDRESS")
        fi
        if ! grep -q "^PRIVATE_KEY=.\{64\}$" "$configfile"; then
            missing_informations+=("PRIVATE_KEY")
        fi
        if ! grep -q "^RESULT_PRIVATE_KEY=.\{64\}$" "$configfile"; then
            missing_informations+=("RESULT_PRIVATE_KEY")
        fi
        if ! grep -q "^RESULT_ADDRESS=0x[[:xdigit:]]\{40\}$" "$configfile"; then
            missing_informations+=("RESULT_ADDRESS")
        fi

        if [ ${#missing_informations[@]} -eq 0 ]; then
            address=$(grep "^ADDRESS=" "$configfile" | cut -d'=' -f2)
            private_key=$(grep "^PRIVATE_KEY=" "$configfile" | cut -d'=' -f2)
            result_private_key=$(grep "^RESULT_PRIVATE_KEY=" "$configfile" | cut -d'=' -f2)
            result_address=$(grep "^RESULT_ADDRESS=" "$configfile" | cut -d'=' -f2)

            if [[ $address =~ ^0x[[:xdigit:]]{40}$ && $result_address =~ ^0x[[:xdigit:]]{40}$ && ${#private_key} -eq 64 && ${#result_private_key} -eq 64 ]]; then
                task_price_check
            else
                echo "Invalid ADDRESS, RESULT_ADDRESS, PRIVATE_KEY, or RESULT_PRIVATE_KEY format or length in the config file."
                echo "Please update the config file with valid information."
                exit 1
            fi
        else
            echo "The following informations are missing or not valid in the config file:"
            for info in "${missing_informations[@]}"; do
                echo "$info"
            done
            echo "Please update or check the config file."
            exit 1
        fi
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
		if [ -f $nodefolder/$configfile ]; then echo "Config file generated successfully. Continuing..." && task_price_check; else echo "Something went wrong. Seek Help!" && exit; fi
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
