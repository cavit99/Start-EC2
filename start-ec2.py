# MIT License
# Copyright (c) 2024 Cavit Erginsoy

import boto3
import logging
import sys
import requests
import yaml
import subprocess
import time
import shutil
import botocore.config
import threading
from botocore.exceptions import NoCredentialsError, ClientError

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    handlers=[
        logging.FileHandler('start-ec2.log'),
        logging.StreamHandler(sys.stdout)  # This will output to the console
    ]
)

# Load the configuration file
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

# Use the configuration values
LAUNCH_TEMPLATE_ID = config['template']
REMOTE_PORT_NUMBER = config['remote_port']
LOCAL_PORT_NUMBER = config['local_port']
awsregion = config['region']
awstagvalue = config['tag_value']


logging.info(f"Using Launch Template ID: {LAUNCH_TEMPLATE_ID}")
logging.info(f"Using Remote Port Number: {REMOTE_PORT_NUMBER}")
logging.info(f"Using Local Port Number: {LOCAL_PORT_NUMBER}")
logging.info(f"Using AWS region: {awsregion}")
logging.info(f"Using AWS tag value: {awstagvalue}")

confirmation = input("Press Enter to continue or type q to exit: ")
if confirmation:
    logging.info("Exiting...")
    sys.exit()

def is_connected():
    try:
        requests.get('http://www.google.com', timeout=5)
        return True
    except requests.exceptions.RequestException:
        return False

def is_aws_configured() -> boto3.Session:
    try:
        session = boto3.Session()
        credentials = session.get_credentials()
        if credentials is None:
            return None
        return session
    except NoCredentialsError:
        return None
 
def does_launch_template_exist(ec2_client, launch_template_id: str) -> bool:
    try:
        response = ec2_client.describe_launch_templates(
            LaunchTemplateIds=[launch_template_id]
        )
        return bool(response['LaunchTemplates'])
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'InvalidLaunchTemplateId.NotFound':
            logging.error(f"Launch template {launch_template_id} does not exist: {e}")
        elif error_code == 'InvalidLaunchTemplateId.Malformed':
            logging.error(f"The launch template ID {launch_template_id} is malformed: {e}")
        else:
            logging.error(f"An unexpected error occurred: {e}")
        return False
    except Exception as e:
        logging.error(f"An error occurred: {e}")
        return False

# Create a new instance
def run_instance(ec2_resource, ec2_client, launch_template_id: str) -> str:
    logging.info("Creating a new instance...")
    instance = ec2_resource.create_instances(
        LaunchTemplate={'LaunchTemplateId': launch_template_id},
        MaxCount=1,
        MinCount=1
    )[0]
    logging.info(f"Waiting for instance {instance.id} to start...")
    instance.wait_until_running()
    logging.info(f"Instance {instance.id} is now running. Waiting for initialization...")
    
    waiter = ec2_client.get_waiter('instance_status_ok')
    waiter.wait(InstanceIds=[instance.id])
    logging.info(f"Instance {instance.id} has passed status checks.")
    
    waiter = ec2_client.get_waiter('system_status_ok')
    waiter.wait(InstanceIds=[instance.id])
    logging.info(f"System on instance {instance.id} has passed status checks.")
    
    return instance.id

def get_instance_id(ec2_resource, name_tag: str) -> str:
    instances = ec2_resource.instances.filter(
        Filters=[
            {'Name': 'tag:Name', 'Values': [name_tag]},
            {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']}
        ])
    for instance in instances:
        return instance.id
    logging.info(f"No instances with tag value '{name_tag}' found.")
    return ""

def wait_for_instance(ec2_client, instance_id: str, status_type: str) -> None:
    waiter = ec2_client.get_waiter(status_type)
    waiter.wait(InstanceIds=[instance_id])
    logging.info(f"Instance {instance_id} has passed {status_type.replace('_', ' ')}.")

def start_instance_if_stopped(ec2_resource, ec2_client, instance_id: str) -> None:
    instance = ec2_resource.Instance(instance_id)
    if instance.state['Name'] != 'running':
        logging.info(f"Starting instance {instance_id}...")
        instance.start()
        wait_for_instance(ec2_client, instance_id, 'instance_running')
        wait_for_instance(ec2_client, instance_id, 'instance_status_ok')
        wait_for_instance(ec2_client, instance_id, 'system_status_ok')

def check_existing_ssm(ssm, instance_id: str, awsregion: str) -> dict:
    try:
        response = ssm.describe_sessions(
            State='Active',
            Filters=[
                {
                    'key': 'Target',
                    'value': instance_id
                }
            ]
        )
        sessions = response.get('Sessions', [])
        shell_sessions = []
        port_forwarding_sessions = []
        for session in sessions:
            if session['DocumentName'] == 'AWS-StartSSHSession':
                shell_sessions.append(session)
            elif session['DocumentName'] == 'AWS-StartPortForwardingSession':
                port_forwarding_sessions.append(session)
        
        # Log the number of active shell and port forwarding sessions
        logging.info(f"Found {len(shell_sessions)} active shell session(s) for instance {instance_id}.")
        logging.info(f"Found {len(port_forwarding_sessions)} active port forwarding session(s) for instance {instance_id}.")

        if shell_sessions:
            logging.info("Active shell session(s) details:")
            for session in shell_sessions:
                logging.info(f"Session ID: {session['SessionId']}, Started At: {session['StartDate']}")

        if port_forwarding_sessions:
            logging.info("Active port forwarding session(s) details:")
            for session in port_forwarding_sessions:
                logging.info(f"Session ID: {session['SessionId']}, Started At: {session['StartDate']}")

        return {
            'shell_sessions': shell_sessions,
            'port_forwarding_sessions': port_forwarding_sessions
        }
    except ClientError as e:
        logging.error(f"Error checking for existing SSM sessions: {e}")
        return {'shell_sessions': [], 'port_forwarding_sessions': []}
    
def initiate_ssm_session(ssm, instance_id: str, awsregion: str) -> bool:
    logging.info("SSM agent is correctly configured. Attempting to start session...")

    # Check if AWS CLI is installed
    if not shutil.which("aws"):
        logging.error("AWS CLI is not installed or not found in PATH.")
        return False

    # Use the AWS CLI 'aws ssm start-session' command to start an interactive shell session
    start_session_command = [
        "aws",
        "ssm",
        "start-session",
        "--target",
        instance_id,
        "--region",
        awsregion
    ]

    logging.info(f"Running command: {' '.join(start_session_command)}")

    try:
        result = subprocess.run(start_session_command)  
        if result.returncode != 0:
            logging.error(f"Failed to start SSM session: {result.stderr}")
            return False

        logging.info(f"SSM session started successfully.")
        return True

    except subprocess.TimeoutExpired:
        logging.error("SSM session command timed out.")
        return False

    except subprocess.CalledProcessError as e:
        logging.error(f"An error occurred while starting the SSM session: {e}")
        return False

    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        return False
        
def ensure_ssm_session(ssm, instance_id: str, awsregion: str) -> bool:
    # Check for existing SSM sessions
    existing_sessions = check_existing_ssm(ssm, instance_id, awsregion)
    
    if existing_sessions['shell_sessions']:
        logging.info(f"Terminating active SSM shell session for instance {instance_id}...")
        for session in existing_sessions['shell_sessions']:
            ssm.terminate_session(SessionId=session['SessionId'])
        time.sleep(2)  # Ensure the session is terminated before starting a new one

    # Now attempt to start a new SSM shell session
    shell_session_started = start_ssm_shell_session(ssm, instance_id, awsregion)
    logging.info(f"SSM shell session started: {shell_session_started}")

    if existing_sessions['port_forwarding_sessions']:
        logging.info(f"Terminating active SSM port forwarding session for instance {instance_id}...")
        for session in existing_sessions['port_forwarding_sessions']:
            ssm.terminate_session(SessionId=session['SessionId'])
        time.sleep(2)  # Ensure the session is terminated before starting a new one

    # Now attempt to start a new SSM port forwarding session
    port_forwarding_session_started = start_ssm_port_forwarding_session(ssm, instance_id, awsregion, REMOTE_PORT_NUMBER, LOCAL_PORT_NUMBER)
    logging.info(f"SSM port forwarding session started: {port_forwarding_session_started}")

    return shell_session_started and port_forwarding_session_started

def is_ssm_agent_configured(ssm, instance_id: str) -> bool:
    try:
        response = ssm.describe_instance_information(
            InstanceInformationFilterList=[{'key': 'InstanceIds', 'valueSet': [instance_id]}]
        )
        return bool(response['InstanceInformationList'])
    except ClientError as e:
        logging.error(f"ClientError occurred while checking SSM agent configuration: {e}")
        return False

def start_ssm_shell_session(ssm, instance_id: str, awsregion: str) -> bool:
    logging.info("Attempting to start an SSM shell session...")
    shell_session_command = [
        "aws", "ssm", "start-session",
        "--target", instance_id,
        "--region", awsregion
    ]
    try:
        result = subprocess.run(shell_session_command)  

        if result.returncode != 0:
            logging.error(f"Failed to start SSM session: {result.stderr}")
            return False

        logging.info(f"SSM session started successfully.")
        return True

    except subprocess.TimeoutExpired:
        logging.error("SSM session command timed out.")
        return False

    except subprocess.CalledProcessError as e:
        logging.error(f"An error occurred while starting the SSM session: {e}")
        return False

    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        return False

def handle_output(process):
    try:
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            logging.error(f"Error starting port forwarding session: {stderr.decode()}")
        else:
            logging.info(f"Port forwarding session started: {stdout.decode()}")
    except Exception as e:
        logging.error(f"An error occurred while handling the process output: {e}")

def start_ssm_port_forwarding_session(ssm, instance_id: str, awsregion: str, remote_port: str, local_port: str) -> subprocess.Popen:
    logging.info("Attempting to start an SSM port forwarding session in the background...")
    port_forwarding_command = [
        "aws", "ssm", "start-session",
        "--target", instance_id,
        "--region", awsregion,
        "--document-name", "AWS-StartPortForwardingSession",
        "--parameters", f"portNumber={remote_port},localPortNumber={local_port}"
    ]
    try:
        process = subprocess.Popen(port_forwarding_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Start a new thread to handle the process's output
        threading.Thread(target=handle_output, args=(process,)).start()
        return process
    except subprocess.SubprocessError as e:
        logging.error(f"An error occurred while starting the SSM port forwarding session: {e}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred while starting the SSM port forwarding session: {e}")
        return None
    
def terminate_ssm_session(ssm, instance_id: str, awsregion: str) -> None:
    try:
        response = ssm.describe_sessions(
            State='Active', Filters=[{'key': 'Target', 'value': instance_id}]
        )
        sessions = response.get('Sessions', [])
        for session in sessions:
            ssm.terminate_session(SessionId=session['SessionId'])
            logging.info(f"Terminated SSM session {session['SessionId']}")
    except ClientError as e:
        logging.error(f"Error terminating SSM session: {e}")

def main() -> None:
    try:
        if not is_connected():
            logging.error("No internet connection. Please check your connection and try again.")
            return
        
        session = is_aws_configured()
        if session is None:
            logging.error("AWS is not configured. Please enter your AWS credentials.")
            return
        
        logging.info("AWS credentials are configured, proceeding.")

        # Create a Config object with custom settings
        custom_config = botocore.config.Config(
            read_timeout=900,
            connect_timeout=900,
            retries={'max_attempts': 0}
        )

        ec2_resource = session.resource('ec2', region_name=awsregion, config=custom_config)
        ec2_client = session.client('ec2', region_name=awsregion, config=custom_config)
        ssm = session.client('ssm', region_name=awsregion, config=custom_config)

        if not does_launch_template_exist(ec2_client, LAUNCH_TEMPLATE_ID):
            logging.error(f"Launch template {LAUNCH_TEMPLATE_ID} does not exist. Exiting.")
            return

        existing_instance_id = get_instance_id(ec2_resource, awstagvalue)

        if existing_instance_id:
            logging.info(f"An instance with tag value '{awstagvalue}' exists. Instance ID: {existing_instance_id}")
            try:
                start_instance_if_stopped(ec2_resource, ec2_client, existing_instance_id)
            except ClientError as e:
                if 'UnauthorizedOperation' in str(e):
                    logging.error("You do not have the necessary permissions to start instances. Please check your IAM policies.")
                else:
                    logging.error(f"Failed to start instance: {e}")
                return
            instance_id = existing_instance_id
        else:
            try:
                instance_id = run_instance(ec2_resource, ec2_client, LAUNCH_TEMPLATE_ID)
            except ClientError as e:
                if 'UnauthorizedOperation' in str(e):
                    logging.error("You do not have the necessary permissions to create instances. Please check your IAM policies.")
                else:
                    logging.error(f"Failed to create instance: {e}")
                return

        # Start the SSM port forwarding session in the background
        logging.info("About to start the SSM port forwarding session...")
        port_forwarding_process = start_ssm_port_forwarding_session(ssm, instance_id, awsregion, REMOTE_PORT_NUMBER, LOCAL_PORT_NUMBER)
        if port_forwarding_process is None:
            logging.error("Unable to start the SSM port forwarding session. Exiting.")
            return
        else:
            logging.info("SSM port forwarding session started successfully.")

        # Give the port forwarding session a moment to start
        time.sleep(2)

        # Start the SSM shell session
        logging.info("About to start the SSM shell session...")
        if not start_ssm_shell_session(ssm, instance_id, awsregion):
            logging.error("Unable to start the SSM shell session. Exiting.")
            return

        try:
            # Terminate the port forwarding session
            port_forwarding_process.terminate()
            port_forwarding_process.wait(timeout=5)
            logging.info("SSM port forwarding session terminated successfully.")
        except subprocess.TimeoutExpired:
            logging.error("Port forwarding session did not terminate in a timely manner.")
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}")
        finally:
            try:
                logging.info("Terminating SSM sessions...")
                response = ssm.describe_sessions(
                    State='Active', Filters=[{'key': 'Target', 'value': instance_id}]
                )
                sessions = response.get('Sessions', [])
                for session in sessions:
                    ssm.terminate_session(SessionId=session['SessionId'])
                    logging.info(f"Terminated SSM session {session['SessionId']}")
            except Exception as e:
                logging.error(f"Failed to terminate SSM sessions: {e}")

            logging.info("Script execution finished.")
            pass
    except Exception as e:
        logging.error(f"An unexpected error occurred in main: {e}")

if __name__ == "__main__":
    main()