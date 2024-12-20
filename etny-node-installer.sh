#!/bin/bash
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

choose_network() {
  echo "#############################################"
  echo "Please select the network:"
  echo "1. Automatic"
  echo "   The node will run only on one of the network where it has gas."
  echo "   The priority order of the networks are: Polygon Mainnet, bloxberg Mainnet"
  echo "2. Polygon Mainnet (ECLD)"
  echo "3. Polygon Testnet (tECLD)"
  echo "4. bloxberg Mainnet (ETNY)"
  echo "5. bloxberg Testnet (tETNY)"
  echo "6. Quit"
  echo "#############################################"

  while true; do
    read -p "Enter your choice: " choice
    case $choice in
        1)  # Check Ubuntu kernel version
            echo "You selected Automatic. This option will set polygon Mainnet if your wallet has MATIC, otherwise will set bloxberg Mainnet."
            export NETWORK=AUTO
	    read -p "Do you want to use the default RPC? [Y/n]: " use_default_rpc
	    use_default_rpc=${use_default_rpc:-Y}
            if [[ "$use_default_rpc" =~ ^[Nn]$ ]]; then
            read -p "Enter your custom RPC for Bloxberg: " custom_bloxberg_rpc
            export BLOXBERG_RPC_URL=$custom_bloxberg_rpc
            read -p "Enter your custom RPC for Polygon: " custom_polygon_rpc
            export POLYGON_RPC_URL=$custom_polygon_rpc
    	    else
	    echo "Using default RPC settings."
	    export BLOXBERG_RPC_URL=https://bloxberg.ethernity.cloud
	    export POLYGON_RPC_URL=https://polygon-rpc.com
	    fi
            break
            ;;
        2)  # Check Ubuntu kernel version
            echo "You selected Mainnet on POLYGON."
            export NETWORK=POLYGON
            read -p "Do you want to use the default POLYGON RPC? [Y/n]: " use_default_rpc
	    use_default_rpc=${use_default_rpc:-Y}
	    if [[ "$use_default_rpc" =~ ^[Nn]$ ]]; then
            read -p "Enter your custom RPC for Polygon: " custom_polygon_rpc
            export POLYGON_RPC_URL=$custom_polygon_rpc
            else
            echo "Using default RPC settings."
	    export POLYGON_RPC_URL=https://polygon-rpc.com
	    fi
	    break
            ;;
        3)  # Check Ubuntu kernel version
            echo "You selected Open Beta."
            export NETWORK=AMOY
            read -p "Do you want to use the default AMOY RPC? [Y/n]: " use_default_rpc
  	    use_default_rpc=${use_default_rpc:-Y}
	    if [[ "$use_default_rpc" =~ ^[Nn]$ ]]; then
            read -p "Enter your custom RPC for Polygon: " custom_amoy_rpc
            export AMOY_RPC_URL=$custom_amoy_rpc
	    else
            echo "Using default RPC settings."	
     	    export AMOY_RPC_URL=https://rpc-amoy.polygon.technology
	    fi
	    break
	    ;;
        4)  # Check Ubuntu kernel version
            echo "You selected Open Beta."
            export NETWORK=BLOXBERG
            read -p "Do you want to use the default Bloxberg RPC? [Y/n]: " use_default_rpc
            use_default_rpc=${use_default_rpc:-Y}
            if [[ "$use_default_rpc" =~ ^[Nn]$ ]]; then
            read -p "Enter your custom RPC for Bloxberg: " custom_bloxberg_rpc
            export BLOXBERG_RPC_URL=$custom_bloxberg_rpc
            else
            echo "Using default RPC settings."
	    export BLOXBERG_RPC_URL=https://bloxberg.ethernity.cloud
            fi
	    break
            ;;

        5)
            echo "You selected Testnet."
       	    export NETWORK=TESTNET
	    read -p "Do you want to use the default TESTNET RPC? [Y/n]: " use_default_rpc
            use_default_rpc=${use_default_rpc:-Y}
            if [[ "$use_default_rpc" =~ ^[Nn]$ ]]; then
            read -p "Enter your custom RPC for TESTNET: " custom_testnet_rpc
            export TESTNET_RPC_URL=$custom_testnet_rpc
            else
            echo "Using default RPC settings."
            export TESTNET_RPC_URL=https://bloxberg.ethernity.cloud
            fi
            break
            ;;
        6)
            echo "Quitting..."
            exit 0
            ;;
        *)
            echo "Invalid choice. Please select a valid option."
            ;;
    esac
  done
}
task_price_check() {
    current_price=$(grep "TASK_EXECUTION_PRICE" "$nodefolder/$configfile" | cut -d'=' -f2)
    if [ "$current_price" != "" ];
    then
        echo "Task execution price already exists in the config file and is currently set to $current_price ETNY/hour."
    export TASK_EXECUTION_PRICE=$current_price
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
        export TASK_EXECUTION_PRICE=$default_price
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
    export TASK_EXECUTION_PRICE=$taskprice
}

deploy_debian() {
  # Determining if the etny-vagrant service is running
  echo "$os found. Continuing..." 
  choose_network
  task_price_check
  echo "#############################################"
  echo "Finding out if etny-vagrant service is already running..."
  systemctl status "$service" 2>/dev/null | grep "active (running)" >/dev/null
  if [ $? -eq 0 ]; then
    echo "The Ethernity agent node is currently running. The service will be stopped, until the installation is complete."
    read -p "Do you want continue ? (Y/n)" choice
    choice="${choice:-Y}"  # Set the default value to "Y" if the input is empty
    if [[ "$choice" =~ ^[Yy]$ ]]; then
      echo "Stopping the service. Please provide the sudo credentials when asked"
      # Stop the service here
      #
      sudo systemctl stop "$service"
      deploy_ansible
    else
      echo "Please manually stop the service and then restart the setup."
      exit 1
    fi
  else
    echo "The service is not running."
    deploy_ansible
  fi
}


deploy_slackware() {
  # Determining if the etny-vagrant service is running
  echo "$os found. Continuing..."
  choose_network
  task_price_check
  echo "#############################################"
  echo "Finding out if rc.etny service is already running..."
  /etc/rc.d/rc.etny status >/dev/null
  if [ $? -eq 0 ]; then
    echo "The Ethernity agent node is currently running. The service will be stopped, until the installation is complete."
    read -p "Do you want continue ? (Y/n) " choice
    choice="${choice:-Y}"  # Set the default value to "Y" if the input is empty
    if [[ "$choice" =~ ^[Yy]$ ]]; then
      echo "Stopping the service..."
      # Stop the service here
      /etc/rc.d/rc.etny stop 
      deploy_ansible
    else
      echo "Please manually stop the service and then restart the setup."
      exit 1
    fi
  else
    echo "The service is not running."
    deploy_ansible
  fi
}

check_config_file() {
    if [ -f "$configfile" ]; then
        echo "Config file found. Checking configuration"

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
        echo "Configuration check succesful!"
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

    echo "Writing network and price to the config file"

        sed -i "/NETWORK/d" "$nodefolder/$configfile"
        echo "NETWORK="$NETWORK >> "$nodefolder/$configfile"

        sed -i "/TASK_EXECUTION_PRICE/d" "$nodefolder/$configfile"
        echo "TASK_EXECUTION_PRICE="$TASK_EXECUTION_PRICE >> "$nodefolder/$configfile"

    if [[ ! -z $BLOXBERG_RPC_URL ]]; then
        sed -i "/BLOXBERG_RPC_URL/d" "$nodefolder/$configfile"
        echo "BLOXBERG_RPC_URL="$BLOXBERG_RPC_URL >> "$nodefolder/$configfile"
    fi

    if [[ ! -z $TESTNET_RPC_URL ]]; then
        sed -i "/TESTNET_RPC_URL/d" "$nodefolder/$configfile"
        echo "TESTNET_RPC_URL="$TESTNET_RPC_URL >> "$nodefolder/$configfile"
    fi

    if [[ ! -z $POLYGON_RPC_URL ]]; then
        sed -i "/POLYGON_RPC_URL/d" "$nodefolder/$configfile"
        echo "POLYGON_RPC_URL="$POLYGON_RPC_URL >> "$nodefolder/$configfile"
    fi

    if [[ ! -z $AMOY_RPC_URL ]]; then
        sed -i "/AMOY_RPC_URL/d" "$nodefolder/$configfile"
        echo "AMOY_RPC_URL="$AMOY_RPC_URL >> "$nodefolder/$configfile"
    fi

    else
        echo "Config file not found. How would you like to continue?"
        config_file_choice
    fi
}

check_ansible(){
    echo -en "Check ansible version... "
    ANSIBLE_VERSION=`ansible --version 2> /dev/null || echo ""`
    if [[ $ANSIBLE_VERSION = "" ]];
    then 
	echo "ansible not installed on the system, proceeding with setup"
        if [ "$os" = "Debian 12" ]
	then
            echo "Installing latest ansible version..." ;
	    UBUNTU_CODENAME=jammy
            sudo apt -y install gpg
	    wget -O- "https://keyserver.ubuntu.com/pks/lookup?fingerprint=on&op=get&search=0x93C4A3FD7BB9C367" | sudo gpg --dearmour -o /usr/share/keyrings/ansible-archive-keyring.gpg
	    echo "deb [signed-by=/usr/share/keyrings/ansible-archive-keyring.gpg] http://ppa.launchpad.net/ansible/ansible/ubuntu $UBUNTU_CODENAME main" | sudo tee /etc/apt/sources.list.d/ansible.list
            sudo apt update 
            sudo apt -y install ansible
        elif [ "$os" = "Ubuntu 20.04" ] || [ "$os" = "Ubuntu 22.04" ] || [ "$os" = "Ubuntu 24.04" ]
        then
            echo "Installing latest ansible version..." ;
            sudo apt-add-repository --yes --update ppa:ansible/ansible 
            sudo apt update 
            sudo apt -y install software-properties-common ansible
        elif [ "$os" = "Slackware 15" ]
        then
            mkdir -p ansible-setup
            cd ansible-setup
	    echo "Installing sbopkg..."
            wget https://github.com/sbopkg/sbopkg/releases/download/0.38.2/sbopkg-0.38.2-noarch-1_wsr.tgz -o ansible-install.log
            sudo upgradepkg --install-new  sbopkg-0.38.2-noarch-1_wsr.tgz >> ansible-install.log
	    sudo mkdir -p /var/lib/sbopkg/queues
	    sudo mkdir -p /var/lib/sbopkg/SBo/15.0
	    echo "Syncing sbo repository..."
            sudo sbopkg -r >> ansible-install.log
	    echo "Installing ansible from sbo..."
            sudo sqg -p ansible >> ansible-install.log 2>&1
            echo 'Q' | sudo sbopkg -B -e stop -q -k -i ansible >> ansible-install.log 2>&1
	    sudo sbopkg -p | grep -v ansible-core | grep -i ansible > /dev/null 2>&1 
	    if [ $? -eq 0 ]
	    then
	        rm -rf ansible-install.log
            else
		echo "Error occured while installing ansible, please check ansible-setup/ansible-install.log"
		exit
            fi
	    cd ..
	    rm -rf ansible-setup
        else
            echo "Your operating system does not have an ansible setup path. This should not happen."
            exit 1
        fi
    else
        echo "ansible found, skipping setup"
    fi
}

deploy_ansible(){
#if we have the right kernel then we run the ansible-playbook and finish installation
    check_config_file
    check_ansible
    ansible_playbook
}

config_file_choice(){
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
        echo "NETWORK="$NETWORK >> $nodefolder/$configfile
        echo "TASK_EXECUTION_PRICE="$TASK_EXECUTION_PRICE >> $nodefolder/$configfile
	if [[ ! -z $BLOXBERG_RPC_URL ]]; then
	echo "BLOXBERG_RPC_URL=$BLOXBERG_RPC_URL" >> $nodefolder/$configfile
	fi
	if [[ ! -z $POLYGON_RPC_URL ]]; then
	echo "POLYGON_RPC_URL=$POLYGON_RPC_URL" >> $nodefolder/$configfile
	fi
	if [[ ! -z $TESTNET_RPC_URL ]]; then
	echo "TESTNET_RPC_URL=$TESTNET_RPC_URL" >> $nodefolder/$configfile
	fi
	if [[ ! -z $AMOY_RPC_URL ]]; then
	echo "AMOY_RPC_URL=$AMOY_RPC_URL" >> $nodefolder/$configfile
	fi
        if [ -f $nodefolder/$configfile ]; then echo "Config file generated successfully. Continuing..."; else echo "Something went wrong. Seek Help!" && exit; fi
    ;;
    2) 
        export FILE=generate
        check_ansible
        ansible_playbook;;
    3) echo "Exiting..." && exit;;
    *) echo "Invalid choice. Please choose an option below..." && config_file_choice;;
esac
}

ansible_playbook(){
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


ubuntu(){
#Getting which version of Ubuntu is instaled
echo "Ubuntu OS found. Determining version..."
family='Debian';

case $(awk '/^VERSION_ID=/' /etc/*-release 2>/dev/null | awk -F'=' '{ print tolower($2) }' | tr -d '"') in
    20.04) 
        os='Ubuntu 20.04'
        deploy_debian;;
    22.04) 
        os='Ubuntu 22.04'
        deploy_debian;;
    24.04)
        os='Ubuntu 24.04'
        deploy_debian;;
    *) echo "Version not supported. Exiting..."
esac
}

debian(){
#Getting which version of Debian is instaled
echo "Debian found. Determining version..."
family='Debian';
case $(awk '/^VERSION_ID=/' /etc/*-release 2>/dev/null | awk -F'=' '{ print tolower($2) }' | tr -d '"') in
    12)
        os='Debian 12'
        deploy_debian;;
    *) echo "Version not supported. Exiting..."
esac
}

slackware(){
#Getting which version of Slackware is instaled
echo "Slackware found. Determining version..."
family='Slackware';
case $(awk '/^VERSION_ID=/' /etc/*-release 2>/dev/null | awk -F'=' '{ print tolower($2) }' | tr -d '"') in
    15.0)
        os='Slackware 15';
	family='Slackware';
        deploy_slackware;;
    *) echo "Version not supported. Exiting..."
esac
}


start(){
#getting which Linux distribution is installed
echo "Getting distro..."
case $(awk '/^ID=/' /etc/*-release 2>/dev/null | awk -F'=' '{ print tolower($2) }' | tr -d '"') in
    ubuntu) ubuntu;;
    debian) debian;;
    slackware) slackware;;
#   centos) echo "centos distro Found. Not Supported. Exiting...";;
#   manjaro) echo "manjaro distro Found. Not Supported. Exiting...";;
#   arch) echo "arch distro Found. Not Supported. Exiting...";;
#   rhel) echo "red hat  distro Found. Not Supported. Exiting...";;
#   fedora) echo "fedora distro Found. Not Supported. Exiting...";;
    *) echo "Could not determine Distro. Exiting..."
esac
}
start
