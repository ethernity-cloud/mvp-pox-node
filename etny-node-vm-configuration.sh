#!/bin/bash

# Define minimum and recommended settings
MIN_MEMORY_MB=3072 #MB
MIN_CPUS=2
MIN_DISK=80 #GB

REC_MEMORY_MB=7168 #MB
REC_CPUS=2
REC_DISK=200 #GB

# Function to get available resources
get_available_resources() {
    total_memory=$(free -m | awk '/Mem:/ {print $2 - 1024}')
    available_memory=$((total_memory - 1))
    available_cpus=$(nproc)
    
    # Calculate available storage by adding the current Vagrant disk image size
    vagrant_disk_size=$(sudo fdisk -l /var/lib/libvirt/images/mvp-pox-node_etnyvm1.img | awk '/Disk \/var/ {print $3}')
    available_disk_size=$(df -BG / | awk '/\// {print $4}' | sed 's/G//')
    available_disk_size=$(($available_disk_size + $vagrant_disk_size))
}

# Function to modify the Vagrantfile
modify_vagrantfile() {
    sed -i.bak -E "s/(domain.memory[[:space:]]*=[[:space:]]*)[[:digit:]]+/\1$new_memory_MB/" Vagrantfile
    sed -i.bak -E "s/(domain.cpus[[:space:]]*=[[:space:]]*)[[:digit:]]+/\1$new_cpus/" Vagrantfile
    sed -i.bak -E "s/(domain.machine_virtual_size[[:space:]]*=[[:space:]]*)[[:digit:]]+/\1$additional_storage/" Vagrantfile
}

# Get available resources
get_available_resources

echo "Available memory: $available_memory MB, available CPUs: $available_cpus, available disk size: $available_disk_size GB"

# Prompt for memory size
read -p "Enter allocated memory size (RAM MB) (minimum $MIN_MEMORY_MB, recommended $REC_MEMORY_MB): " new_memory_MB

# Check memory
if ((new_memory_MB < MIN_MEMORY_MB || new_memory_MB > available_memory)); then
    echo "Error: Entered memory is invalid."
    exit 1
fi

# Prompt for CPU count
read -p "Enter allocated CPU count (minimum $MIN_CPUS, recommended $REC_CPUS): " new_cpus

# Check CPUs
if ((new_cpus < MIN_CPUS || new_cpus > available_cpus)); then
    echo "Error: Entered CPU count is invalid."
    exit 1
fi

# Prompt for additional storage
read -p "Enter additional allocated storage (GB) (minimum $MIN_DISK, recommended $REC_DISK): " additional_storage

# Check storage
new_total_storage=$((available_disk_size + additional_storage))
if ((additional_storage < MIN_DISK || new_total_storage > available_disk_size)); then
    echo "Error: Entered additional storage is invalid."
    exit 1
fi

# Provide feedback before modifying Vagrantfile
echo "Calculating new storage configuration..."
echo "New total storage: $new_total_storage GB"
echo "Please wait, updating Vagrantfile..."

# Modify Vagrantfile
modify_vagrantfile

echo "Backup of the original Vagrantfile created: Vagrantfile.bak"
echo "Vagrantfile updated successfully!"

# Reload Vagrant
vagrant reload
echo "Vagrant reloaded successfully!"

# Resize VM disk
vagrant ssh -c "sudo lvextend -l +100%FREE /dev/ubuntu-vg/ubuntu-lv"
vagrant ssh -c "sudo resize2fs /dev/mapper/ubuntu--vg-ubuntu--lv"
echo "Disk resized successfully!"
echo "Configuration done"
