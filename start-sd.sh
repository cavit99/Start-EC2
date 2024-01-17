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
  
# User data script to initialize the EBS volume  
user_data=$(cat <<EOF  
#!/bin/bash  
cd /home/ubuntu/stable-diffusion-webui && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull  
cd /home/ubuntu/kohya_ss && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull  
cd /home/ubuntu/ComfyUI && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull  
find /home/ubuntu/stable-diffusion-webui/extensions -maxdepth 2 -type d -exec sh -c 'cd {} && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull' \;  
find /home/ubuntu/ComfyUI/custom_nodes -maxdepth 2 -type d -exec sh -c 'cd {} && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull' \;  
sudo apt-get update  
nohup fio --filename=/dev/nvme0n1 --rw=read --bs=1M --iodepth=32 --ioengine=libaio --direct=1 --name=volume-initialize &  
EOF  
)  
  
# Check if an instance with the tag 'sd' is already running  
existing_instance_id=$(aws ec2 describe-instances --region eu-north-1 --filters "Name=tag:Name,Values=sd" | jq -r '.Reservations[].Instances[] | select(.State.Name=="running") | .InstanceId' | head -n 1)  
  
if [ $? -ne 0 ]; then  
    log "Failed to describe instances. Quitting"  
    exit 1  
fi  
  
if [ -n "$existing_instance_id" ]; then  
    log "An instance with SD tag is already running. Instance ID: $existing_instance_id"  
    instance_id=$existing_instance_id  
else  
    # Run the instance and get the instance ID  
    instance_id=$(aws ec2 run-instances --region eu-north-1 --launch-template LaunchTemplateId=lt-0e096fddfc03f20b6 --user-data "$user_data" | jq -r '.Instances[0].InstanceId')  
  
    # Check if instance_id is null or empty  
    if [ -z "$instance_id" ]; then  
        log "Failed to create instance."  
        exit 1  
    fi  
  
    # Wait for the instance to be in a running state  
    log "Waiting for the instance to be in a running state..."  
  
    counter=0  
    while true; do  
        instance_state=$(aws ec2 describe-instances --region eu-north-1 --instance-ids $instance_id | jq -r '.Reservations[0].Instances[0].State.Name')  
  
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
fi  
  
# Start a session with the instance using AWS Systems Manager  
if aws ssm start-session  --target $instance_id --document-name AWS-StartPortForwardingSession --parameters '{"portNumber":["8188"],"localPortNumber":["8188"]}'; then  
    log "Successfully started a session with the instance."  
else  
    log "Failed to start a session with the instance."  
    exit 1  
fi  
