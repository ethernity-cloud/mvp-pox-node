#!/bin/bash

# Define minimum and recommended settings
MIN_MEMORY_MB=3072 #MB
MIN_CPUS=2
MIN_DISK=128 #GB

REC_MEMORY_MB=7168 #MB
REC_CPUS=4
REC_DISK=200 #GB

# Function to get available resources
get_available_resources() {
    total_memory=$(free -m | awk '/Mem:/ {print $2 - 1024}')
    available_memory=$((total_memory - 1))
    available_cpus=$(nproc)

# Calculate available storage by adding the current Vagrant disk image size
vagrant_disk_size=$(sudo fdisk -l /var/lib/libvirt/images/mvp-pox-node_etnyvm1.img | awk '/Disk \/var/ {print $3}')
available_disk_size=$(df -BG / | awk '/\// {print $4}' | sed 's/G//')
available_disk_size=$(echo "$available_disk_size + $vagrant_disk_size" | bc)

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
read -p "Enter allocated storage (GB) (minimum $MIN_DISK, recommended $REC_DISK): " additional_storage

# Calculate the new total storage size
total_storage=$(echo "$available_disk_size + $additional_storage" | bc)

# Compare storage sizes as strings
if [[ $(echo "$additional_storage > $total_storage" | bc) -eq 1 ]]; then
    echo "Error: Entered additional storage is greater than the available storage."
    exit 1
fi

if [[ $(echo "$additional_storage < $MIN_DISK" | bc) -eq 1 ]]; then
    echo "Error: Entered additional storage is less than the minimum required."
    exit 1
fi

# Modify Vagrantfile
modify_vagrantfile

echo "Backup of the original Vagrantfile created: Vagrantfile.bak"
echo "Vagrantfile updated successfully!"
echo "Reloading vagrant..."

# Reload Vagrant
vagrant reload > /dev/null 2>&1
sleep 60
vagrant up > /dev/null 2>&1
echo "Vagrant reloaded successfully!"

# Resize Memory
echo "Setting up memory size"
VAGRANT_ID=`vagrant status | grep etny | awk '{print $1}'`
VMID=`virsh list | grep $VAGRANT_ID | awk '{print $2}'`
virsh setmem --domain ${VMID} ${new_memory_MB}M --live

# Resize VM disk
echo "Extending partition"
vagrant ssh -c "sudo lvextend -l+100%FREE /dev/ubuntu-vg/ubuntu-lv"
vagrant ssh -c "sudo resize2fs /dev/mapper/ubuntu--vg-ubuntu--lv"
echo "Configuration done"
