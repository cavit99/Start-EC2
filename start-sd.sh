#!/bin/bash

# Define a log file
logfile="start-sd.log"

# Function to log messages
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a $logfile
}

# Check if AWS CLI is configured
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    log "AWS CLI is not configured. Please configure it and try again."
    exit 1
fi

# Get the local public IP
local_ip=$(curl -s ifconfig.me)

# Check if local_ip is null or empty
if [ -z "$local_ip" ]; then
    log "Failed to get local public IP."
    exit 1
fi

# Check if the IP already exists in the security group
ip_exists=$(aws ec2 describe-security-groups --group-ids sg-0a2f9548b660b5d0a --region eu-north-1 | jq -r ".SecurityGroups[0].IpPermissions[] | select(.FromPort == 0 and .ToPort == 65535) | .IpRanges[] | select(.CidrIp == \"$local_ip/32\")")

if [ -z "$ip_exists" ]; then
    # Add the local public IP to the security group
    if aws ec2 authorize-security-group-ingress --group-id sg-0a2f9548b660b5d0a --protocol tcp --port 0-65535 --cidr $local_ip/32 --region eu-north-1 > /dev/null 2>&1; then
        log "Successfully added IP to security group."
    else
        log "Failed to add IP to security group."
        exit 1
    fi
fi

# User data script to initialize the EBS volume
user_data=$(cat <<EOF
#!/bin/bash
cd /home/ubuntu/stable-diffusion-webui && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull
cd /home/ubuntu/kohya_ss && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull
cd /home/ubuntu/ComfyUI && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull
find /home/ubuntu/stable-diffusion-webui/extensions -type d -exec sh -c 'cd {} && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull' \;
find /home/ubuntu/ComfyUI/custom_nodes -type d -exec sh -c 'cd {} && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull' \;
sudo apt-get update
nohup fio --filename=/dev/nvme0n1 --rw=read --bs=1M --iodepth=32 --ioengine=libaio --direct=1 --name=volume-initialize &
EOF
)

# Run the instance and get the instance ID
instance_id=$(aws ec2 run-instances \
    --region eu-north-1 \
    --launch-template LaunchTemplateId=lt-0e096fddfc03f20b6 \
    --user-data "$user_data" \
    | jq -r '.Instances[0].InstanceId')

# Check if instance_id is null or empty
if [ -z "$instance_id" ]; then
    log "Failed to create instance."
    exit 1
fi

# Wait for the instance to be in a running state
counter=0
while true; do
    instance_state=$(aws ec2 describe-instances \
        --region eu-north-1 \
        --instance-ids $instance_id \
        | jq -r '.Reservations[0].Instances[0].State.Name')

    if [ "$instance_state" == "running" ]; then
        log "Instance is running."
        break
    elif [ "$instance_state" == "pending" ]; then
        sleep 10
        counter=$((counter+1))
        if [ $counter -gt 30 ]; then
            log "Instance is not in running state after 5 minutes. Exiting."
            exit 1
        fi
    else
        log "Instance is in an unexpected state: $instance_state"
        exit 1
    fi
done

# Wait for the public IP address to become available
counter=0
while true; do
    ip_address=$(aws ec2 describe-instances \
        --region eu-north-1 \
        --instance-ids $instance_id \
        | jq -r '.Reservations[0].Instances[0].PublicIpAddress')

    if [ "$ip_address" != "null" ]; then
        log "Public IP address is available."
        break
    fi

    sleep 10
    counter=$((counter+1))
    if [ $counter -gt 30 ]; then
        log "Public IP address is not available after 5 minutes. Exiting."
        exit 1
    fi
done

# Update the SSH config file
if sed -i '' "/^Host sd$/,/^$/s/HostName .*/HostName $ip_address/" ~/.ssh/config; then
    log "SSH config file updated successfully."
else
    log "Failed to update SSH config file."
    exit 1
fi

# Connect to the instance
counter=0
while true; do
    if ping -c 1 $ip_address &> /dev/null; then
        if ssh sd -L 7860:localhost:7860; then
            log "Successfully connected to the instance."
            break
        else
            log "Failed to connect to the instance."
            exit 1
        fi
    else
        counter=$((counter+1))
        if [ $counter -gt 3 ]; then
            log "Failed to connect to the instance after 3 attempts. Please check your network and try again."
            exit 1
        fi
        log "Ping unsuccessful. Waiting for 10 seconds before trying again."
        sleep 10
    fi
done