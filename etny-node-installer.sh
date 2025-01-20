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
    local ini_file="networks.ini"
    declare -A networks          # Maps option numbers to network names
    declare -A shortnames        # Maps option numbers to network shortnames
    declare -A descriptions      # Maps option numbers to descriptions
    declare -A rpc_vars         # Maps option numbers to RPC environment variable names
    declare -A rpc_defaults     # Maps option numbers to default RPC URLs

    # Associative array to store selected networks' RPC URLs
    declare -A selected_rpc_urls
    declare -a selected_choices  # Maintains the order of selections

    # Array to store selected network names
    declare -a selected_networks

    # Array to store selected network short names
    declare -a selected_shortnames

    declare -A gas_vars         # Maps option numbers to GAS environment variable names
    declare -A gas_defaults     # Maps option numbers to default GAS values
    declare -a selected_gas_at_start


    # Function to trim whitespace
    trim() {
        local var="$*"
        var="${var#"${var%%[![:space:]]*}"}"   # Remove leading whitespace
        var="${var%"${var##*[![:space:]]}"}"   # Remove trailing whitespace
        echo -n "$var"
    }

    # Parse the ini file
    current_section=""
    while IFS= read -r line || [ -n "$line" ]; do
        # Remove comments and trim
        line=$(echo "$line" | sed 's/[;#].*$//' | xargs)
        [[ -z "$line" ]] && continue

        if [[ "$line" =~ ^\[(.*)\]$ ]]; then
            current_section="${BASH_REMATCH[1]}"
            continue
        fi

        if [[ "$current_section" =~ ^[0-9]+$ ]]; then
            key=$(echo "$line" | cut -d'=' -f1)
            value=$(echo "$line" | cut -d'=' -f2-)
            value=$(trim "$value")

            if [[ "$key" == "name" ]]; then
                networks["$current_section"]="$value"
            elif [[ "$key" == "shortname" ]]; then
                shortnames["$current_section"]="$value"
            elif [[ "$key" == "description" ]]; then
                descriptions["$current_section"]="$value"
            elif [[ "$key" == "RPC_URL" ]]; then
                rpc_vars["$current_section"]="$key"
                rpc_defaults["$current_section"]="$value"
            elif [[ "$key" == "MINIMUM_GAS_AT_START" ]]; then
                gas_vars["$current_section"]="$key"
                gas_defaults["$current_section"]="$value"
            fi
        fi
    done < "$ini_file"

    # Check if networks are loaded
    if [ ${#networks[@]} -eq 0 ]; then
        echo "No networks found in $ini_file."
        return 1
    fi

    # Display the menu
    echo ""
    echo "###################  NETWORK SETTINGS  ###################"
    echo "Please select one or more networks (separated by spaces or commas):"
    echo "0. AUTO (Select all networks)"
    echo "   When AUTO is selected, the node will operate on"
    echo "   all available networks."
    for key in $(echo "${!networks[@]}" | tr ' ' '\n' | sort -n); do
        echo "$key. ${networks[$key]}"
    done
    echo "#########################################################"

    # Prompt user for selection
    while true; do
        read -p "Enter your choices: " user_input

        if [ "$user_input" == "" ]
        then
            user_input="0";
        fi

        # Replace commas with spaces and split into an array
        IFS=',' read -ra choices <<< "$user_input"
        # Trim each choice
        for i in "${!choices[@]}"; do
            choices[i]=$(echo "${choices[i]}" | xargs)
        done

        # Remove empty choices
        choices=($(printf "%s\n" "${choices[@]}" | grep -v '^$'))

        # Check if AUTO is selected
        if [[ " ${choices[@]} " =~ " 0 " ]]; then
            echo "You selected: AUTO (All Networks)"
            # Select all network keys
            selected_keys=($(printf "%s\n" "${!networks[@]}" | sort -n))
            for key in "${selected_keys[@]}"; do
                selected_networks+=("${networks[$key]}")
                selected_rpc_urls["$key"]="${rpc_defaults[$key]}"
                selected_gas_at_start["$key"]="${gas_defaults[$key]}"
                selected_choices+=("$key")  # Maintain order
            done
            export NETWORK="AUTO"
            break
        fi

        # Validate all choices
        valid=true
        for choice in "${choices[@]}"; do
            if [[ -z "${networks[$choice]}" ]]; then
                echo "Invalid choice: $choice. Please try again."
                valid=false
                break
            fi
        done

        if [ "$valid" = true ]; then
            # Process selected networks
            for choice in "${choices[@]}"; do
                if [[ ! " ${selected_choices[@]} " =~ " $choice " ]]; then
                    selected_choices+=("$choice")  # Maintain order
                    selected_name="${networks[$choice]}"
                    selected_networks+=("$selected_name")
                    selected_shortnames+=("${shortnames[$choice]}")
                    selected_rpc_urls["$choice"]="${rpc_defaults[$choice]}"
                    selected_gas_at_start["$choice"]="${gas_defaults[$choice]}"
                fi
            done

            # Export the NETWORK variable as a space-separated list
            export NETWORK="${selected_shortnames[*]}"
            break
        fi
    done



    # Function to display current RPC settings
    display_rpc_settings() {
        echo ""
        echo "################ RPC SETTINGS ################"
        for choice in "${selected_choices[@]}"; do
            rpc_var="${rpc_vars[$choice]}"
            rpc_url="${selected_rpc_urls[$choice]}"
            echo "$choice. ${networks[$choice]}: $rpc_url"
        done
        echo "0. Continue"
        echo "###############################################"
    }

    # Initial RPC settings are already set to defaults from ini
    # Export RPC environment variables
    for choice in "${selected_choices[@]}"; do
        rpc_var="${rpc_vars[$choice]}"
        rpc_url="${selected_rpc_urls[$choice]}"
        export "$rpc_var"="$rpc_url"
    done



    # Loop to allow user to modify RPC settings
    while true; do
        # Display current RPC settings
        display_rpc_settings

        read -p "Enter the number of the network to change its RPC URL, or 0 to continue: " modify_choice

        # Default to 0 if no input
        modify_choice=${modify_choice:-0}

        if [[ "$modify_choice" == "0" ]]; then
            echo "Continuing with the current RPC settings."
            break
        fi

        # Check if the choice is valid and selected
        if [[ -z "${networks[$modify_choice]}" ]] || [[ -z "${selected_rpc_urls[$modify_choice]}" ]]; then
            echo "Invalid choice. Please try again."
            continue
        fi

        # Get network details
        selected_name="${networks[$modify_choice]}"
        selected_rpc_var="${rpc_vars[$modify_choice]}"
        current_rpc="${selected_rpc_urls[$modify_choice]}"


        # Prompt to change RPC URL
        while true; do
                read -p "Enter the new RPC URL for $selected_name: " new_rpc
                if [[ "$new_rpc" =~ ^https?://.+ ]]; then
                    selected_rpc_urls["$modify_choice"]="$new_rpc"
                    export "$selected_rpc_var"="$new_rpc"
                    break
                else
                    echo "Invalid URL format. Please enter a valid URL (e.g., https://example.com)."
                fi
        done
    done

    # Final confirmation of RPC settings
    echo "Final RPC settings:"
    for choice in "${!selected_rpc_urls[@]}"; do
        rpc_var="${rpc_vars[$choice]}"
        rpc_url="${selected_rpc_urls[$choice]}"
        echo "$rpc_var=$rpc_url"
    done

  # ------------------- Export to Ansible and ENV Vars Files -------------------
    # Define the output vars file path
    vars_dir="roles/validate_config_file/vars"
    vars_file="$vars_dir/main.yml"

    # Create the vars directory if it doesn't exist
    mkdir -p "$vars_dir"

    # Initialize the vars file with the 'networks' key
    echo "networks:" > "$vars_file"

    # Append each selected network's details in YAML format
    for choice in "${selected_choices[@]}"; do
        name="${networks[$choice]}"
        shortname="${shortnames[$choice]}"
        description="${descriptions[$choice]}"
        rpc_url="${selected_rpc_urls[$choice]}"
        gas_at_start="${selected_gas_at_start[$choice]}"

        echo "  - name: \"$name\"" >> "$vars_file"
        echo "    shortname: \"$shortname\"" >> "$vars_file"
        echo "    description: \"$description\"" >> "$vars_file"
        echo "    rpc_url: \"$rpc_url\"" >> "$vars_file"
        echo "    minimum_gas_at_start: $gas_at_start" >> "$vars_file"  
        sed -i "/^${shortnames[$choice]}_RPC_URL=/d" "$nodefolder/$configfile"
        sed -i "/^${shortnames[$choice]}_MINIMUM_GAS_AT_START=/d" "$nodefolder/$configfile"
        echo "${shortnames[$choice]}_RPC_URL=${rpc_url}" >> $nodefolder/$configfile
        echo "${shortnames[$choice]}_MINIMUM_GAS_AT_START=${gas_at_start}" >> $nodefolder/$configfile

    done

    echo ""
    echo "Ansible variables have been exported to $vars_file"
    echo "You can include this file in your Ansible playbooks or roles."

}


task_price_check() {
    current_price=$(grep "TASK_EXECUTION_PRICE" "$nodefolder/$configfile" | cut -d'=' -f2)
    echo ""
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
        echo "Do you want to use the default value of 1 ETNY/hour? (Y/n)"
        read -r use_default
        if [[ -z "$use_default" ]] || [[ "$use_default" =~ ^[Yy]$ ]]; then
            default_price=1
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
    if [ -f "$nodefolder/$configfile" ]; then
        echo "Config file found. Checking configuration"

        # Validate PRIVATE_KEY length
        private_key=$(grep "^PRIVATE_KEY=" "$nodefolder/$configfile" | cut -d'=' -f2)
        if [[ ${#private_key} -ne 64 ]]; then
            echo "Private key invalid or not found in config file. How would you like to continue?"
            config_file_choice
        fi

        echo "Configuration check successful!"
        echo "Writing network and price to the config file"

        # Remove existing entries and append the current values
        sed -i "/^NETWORK=/d" "$nodefolder/$configfile"
        echo "NETWORK=$NETWORK" >> "$nodefolder/$configfile"

        sed -i "/^TASK_EXECUTION_PRICE=/d" "$nodefolder/$configfile"
        echo "TASK_EXECUTION_PRICE=$TASK_EXECUTION_PRICE" >> "$nodefolder/$configfile"

        # Loop through each RPC variable and write to config file if set
        for rpc_var in "${rpc_vars[@]}"; do
            rpc_value="${!rpc_var}"
            if [[ ! -z "$rpc_value" ]]; then
                sed -i "/^$rpc_var=/d" "$nodefolder/$configfile"
                echo "$rpc_var=$rpc_value" >> "$nodefolder/$configfile"
            fi
        done

        echo "Configuration updated successfully."

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
echo ""
echo "******************* GENERATE WALLET *******************"
echo "1. Enter private key"
echo "2. Generate private key"
echo "3. Exit"

echo -n "How do you want to setup the private key? (default: 2):" && read choice
case "$choice" in 
    1) 
        echo "Type/Paste wallet details below..."
        nodeaddr=("Node Private Key: ")
        IFS=""
        for address in ${nodeaddr[@]}; do
            case $address in
                ${nodeaddr[0]})
                while true
                do
                    echo -n $address && read nodeprivatekey 
                    if [[ $nodeprivatekey = "" ]]; then echo "Node private key cannot be empty."; else break; fi
                done;;
            esac
        done

        sed -i "/^PRIVATE_KEY=/d" "$nodefolder/$configfile"

        echo "PRIVATE_KEY="$nodeprivatekey >> $nodefolder/$configfile
    ;;
    3) echo "Exiting..." && exit;;
    *)
        sed -i "/^PRIVATE_KEY=/d" "$nodefolder/$configfile"
        GENERATED="`utils/ethkey generate random`"
        PRIVATE_KEY="`echo ${GENERATED} | awk '{print $2}'`"
        ADDRESS="`echo ${GENERATED} | awk '{print $6}'`"

        echo "PRIVATE_KEY="$PRIVATE_KEY >> $nodefolder/$configfile

        echo "Add gas to the following address: ${ADDRESS}"
        read -p "Once the transaction is complete, press any key to continue..." continue

        check_ansible
        ansible_playbook;;

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
